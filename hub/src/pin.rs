//! Operator PIN: a short code for signing in to the hub on the field laptop.
//!
//! * Set it on the hub computer only: `strix-hub set-pin hub.toml` (never through the web page).
//! * Stored as a salted PBKDF2-HMAC-SHA256 hash in `<data_dir>/hub_pin` (mode 0600).
//! * A short PIN is guessable, so wrong tries lock sign-in for everyone: 5 wrong → 1 min,
//!   then 2, 4, 8… up to 15 min. Combined with the per-client limit, guessing a 6-digit
//!   PIN would take years.

use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use base64::engine::general_purpose::STANDARD_NO_PAD as B64;
use base64::Engine;

use crate::auth::ct_eq;

pub const ITERATIONS: u32 = 200_000;
const FAILS_PER_LOCK: u32 = 5;
const MAX_LOCK: Duration = Duration::from_secs(15 * 60);

#[derive(Debug, PartialEq)]
pub enum PinError {
    NotSet,
    Locked(u64),
    Wrong { tries_left: u32 },
}

#[derive(Default)]
struct Lockout {
    fails: u32,
    until: Option<Instant>,
}

pub struct PinStore {
    hash: Option<String>,
    lock: Mutex<Lockout>,
}

/// 6–8 digits, not all the same and not a straight run like 123456 or 987654.
pub fn check_strength(pin: &str) -> Result<(), &'static str> {
    if !(6..=8).contains(&pin.len()) || !pin.bytes().all(|b| b.is_ascii_digit()) {
        return Err("use 6 to 8 digits");
    }
    let d: Vec<i8> = pin.bytes().map(|b| (b - b'0') as i8).collect();
    if d.iter().all(|x| *x == d[0]) {
        return Err("too easy: all digits are the same");
    }
    let step = d[1] - d[0];
    if step.abs() == 1 && d.windows(2).all(|w| w[1] - w[0] == step) {
        return Err("too easy: digits in a row");
    }
    Ok(())
}

fn derive(pin: &str, salt: &[u8], iterations: u32) -> [u8; 32] {
    let mut out = [0u8; 32];
    pbkdf2::pbkdf2_hmac::<sha2::Sha256>(pin.as_bytes(), salt, iterations, &mut out);
    out
}

pub fn hash_pin(pin: &str, iterations: u32) -> String {
    let mut salt = [0u8; 16];
    getrandom::getrandom(&mut salt).expect("OS random source");
    format!(
        "pbkdf2-sha256${iterations}${}${}",
        B64.encode(salt),
        B64.encode(derive(pin, &salt, iterations))
    )
}

pub fn verify(stored: &str, pin: &str) -> bool {
    let parts: Vec<&str> = stored.trim().split('$').collect();
    let [scheme, iters, salt, hash] = parts[..] else {
        return false;
    };
    let (Ok(iters), Ok(salt)) = (iters.parse::<u32>(), B64.decode(salt)) else {
        return false;
    };
    if scheme != "pbkdf2-sha256" || !(1_000..=10_000_000).contains(&iters) {
        return false;
    }
    ct_eq(&B64.encode(derive(pin, &salt, iters)), hash)
}

pub fn write_pin_file(path: &Path, pin: &str) -> std::io::Result<()> {
    use std::io::Write;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    writeln!(opts.open(path)?, "{}", hash_pin(pin, ITERATIONS))
}

impl PinStore {
    pub fn load(path: &Path) -> PinStore {
        let hash = std::fs::read_to_string(path)
            .ok()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());
        PinStore::new(hash)
    }

    pub fn new(hash: Option<String>) -> PinStore {
        PinStore {
            hash,
            lock: Mutex::new(Lockout::default()),
        }
    }

    pub fn enabled(&self) -> bool {
        self.hash.is_some()
    }

    pub fn locked_for(&self) -> u64 {
        let l = self.lock.lock().unwrap();
        l.until
            .map(|u| {
                u.saturating_duration_since(Instant::now())
                    .as_secs_f64()
                    .ceil() as u64
            })
            .unwrap_or(0)
    }

    pub fn attempt(&self, pin: &str) -> Result<(), PinError> {
        let Some(hash) = &self.hash else {
            return Err(PinError::NotSet);
        };
        let left = self.locked_for();
        if left > 0 {
            return Err(PinError::Locked(left));
        }
        // Hashing is deliberately slow; do not hold the lock while doing it.
        let ok = pin.len() <= 16 && verify(hash, pin);
        let mut l = self.lock.lock().unwrap();
        if ok {
            *l = Lockout::default();
            return Ok(());
        }
        l.fails += 1;
        if l.fails.is_multiple_of(FAILS_PER_LOCK) {
            let round = l.fails / FAILS_PER_LOCK - 1;
            let dur = Duration::from_secs(60u64 << round.min(10)).min(MAX_LOCK);
            l.until = Some(Instant::now() + dur);
            return Err(PinError::Locked(dur.as_secs()));
        }
        Err(PinError::Wrong {
            tries_left: FAILS_PER_LOCK - l.fails % FAILS_PER_LOCK,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strength() {
        assert!(check_strength("482915").is_ok());
        assert!(check_strength("48291573").is_ok());
        assert!(check_strength("12345").is_err());
        assert!(check_strength("123456789").is_err());
        assert!(check_strength("12a456").is_err());
        assert!(check_strength("000000").is_err());
        assert!(check_strength("123456").is_err());
        assert!(check_strength("876543").is_err());
        assert!(check_strength("135790").is_ok());
    }

    #[test]
    fn hash_and_verify() {
        let h = hash_pin("482915", 1_000);
        assert!(h.starts_with("pbkdf2-sha256$1000$"));
        assert!(verify(&h, "482915"));
        assert!(!verify(&h, "482916"));
        assert_ne!(h, hash_pin("482915", 1_000), "salted");
        assert!(!verify("garbage", "482915"));
        assert!(
            !verify("pbkdf2-sha256$1$AAAA$AAAA", "482915"),
            "too few iterations refused"
        );
    }

    #[test]
    fn lockout_after_five_wrong() {
        let s = PinStore::new(Some(hash_pin("482915", 1_000)));
        assert_eq!(s.attempt("111112"), Err(PinError::Wrong { tries_left: 4 }));
        for _ in 0..3 {
            assert!(matches!(s.attempt("111112"), Err(PinError::Wrong { .. })));
        }
        assert_eq!(s.attempt("111112"), Err(PinError::Locked(60)));
        // even the right PIN is refused while locked
        assert!(matches!(s.attempt("482915"), Err(PinError::Locked(_))));
        assert!(s.locked_for() > 50);
    }

    #[test]
    fn success_resets_and_unset_is_refused() {
        let s = PinStore::new(Some(hash_pin("482915", 1_000)));
        let _ = s.attempt("000001");
        assert_eq!(s.attempt("482915"), Ok(()));
        assert_eq!(s.attempt("000001"), Err(PinError::Wrong { tries_left: 4 }));
        assert_eq!(PinStore::new(None).attempt("482915"), Err(PinError::NotSet));
    }
}
