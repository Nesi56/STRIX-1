//! Dashboard login (same scheme as the drone service): one access token, exchanged
//! at `POST /api/auth/login` for a signed, HttpOnly, SameSite=Strict cookie.

use std::collections::{HashMap, VecDeque};
use std::io::Write;
use std::net::IpAddr;
use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;

pub const COOKIE: &str = "strix_hub";

pub struct Auth {
    token: String,
    key: [u8; 32],
    pub ttl_s: u64,
    attempts: Mutex<HashMap<IpAddr, VecDeque<Instant>>>,
}

pub fn random_token(bytes: usize) -> String {
    let mut buf = vec![0u8; bytes];
    getrandom::getrandom(&mut buf).expect("OS random source");
    URL_SAFE_NO_PAD.encode(buf)
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

pub fn ct_eq(a: &str, b: &str) -> bool {
    !a.is_empty() && a.len() == b.len() && bool::from(a.as_bytes().ct_eq(b.as_bytes()))
}

/// Read a secret from `env`, else from `file`, else generate one into `file` (mode 0600).
/// Returns (secret, generated?). The value is never logged.
pub fn load_or_create_secret(env: Option<&str>, file: &Path) -> std::io::Result<(String, bool)> {
    if let Some(var) = env {
        if let Ok(v) = std::env::var(var) {
            if !v.trim().is_empty() {
                return Ok((v.trim().to_string(), false));
            }
        }
    }
    if let Ok(v) = std::fs::read_to_string(file) {
        if !v.trim().is_empty() {
            return Ok((v.trim().to_string(), false));
        }
    }
    let secret = random_token(24);
    if let Some(dir) = file.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut f = opts.open(file)?;
    writeln!(f, "{secret}")?;
    Ok((secret, true))
}

impl Auth {
    pub fn new(token: String, ttl_hours: u64) -> Auth {
        // Rotating the token invalidates every session.
        let key: [u8; 32] = Sha256::digest(format!("strix-hub-session:{token}").as_bytes()).into();
        Auth {
            token,
            key,
            ttl_s: ttl_hours * 3600,
            attempts: Mutex::new(HashMap::new()),
        }
    }

    pub fn check_token(&self, candidate: &str) -> bool {
        ct_eq(candidate.trim(), &self.token)
    }

    fn sign(&self, body: &str) -> String {
        let mut mac = Hmac::<Sha256>::new_from_slice(&self.key).expect("hmac key");
        mac.update(body.as_bytes());
        mac.finalize()
            .into_bytes()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    }

    pub fn make_cookie(&self) -> String {
        let payload = serde_json::json!({ "exp": now() + self.ttl_s as f64, "n": random_token(8) });
        let body = URL_SAFE_NO_PAD.encode(payload.to_string());
        let sig = self.sign(&body);
        format!("{body}.{sig}")
    }

    pub fn verify_cookie(&self, value: &str) -> bool {
        let Some((body, sig)) = value.rsplit_once('.') else {
            return false;
        };
        if !ct_eq(sig, &self.sign(body)) {
            return false;
        }
        let Ok(raw) = URL_SAFE_NO_PAD.decode(body) else {
            return false;
        };
        let Ok(v) = serde_json::from_slice::<serde_json::Value>(&raw) else {
            return false;
        };
        v.get("exp")
            .and_then(|e| e.as_f64())
            .is_some_and(|exp| exp > now())
    }

    /// Bearer token or session cookie.
    pub fn is_authenticated(&self, headers: &axum::http::HeaderMap) -> bool {
        if let Some(auth) = headers.get("authorization").and_then(|v| v.to_str().ok()) {
            if auth.len() > 7
                && auth[..7].eq_ignore_ascii_case("bearer ")
                && self.check_token(&auth[7..])
            {
                return true;
            }
        }
        cookie_value(headers, COOKIE).is_some_and(|c| self.verify_cookie(&c))
    }

    /// At most 10 login attempts per client per minute.
    pub fn login_allowed(&self, ip: IpAddr) -> bool {
        let mut map = self.attempts.lock().unwrap();
        let q = map.entry(ip).or_default();
        let cutoff = Instant::now() - Duration::from_secs(60);
        while q.front().is_some_and(|t| *t < cutoff) {
            q.pop_front();
        }
        if q.len() >= 10 {
            return false;
        }
        q.push_back(Instant::now());
        true
    }
}

pub fn cookie_value(headers: &axum::http::HeaderMap, name: &str) -> Option<String> {
    headers
        .get_all("cookie")
        .iter()
        .filter_map(|v| v.to_str().ok())
        .flat_map(|v| v.split(';'))
        .find_map(|kv| {
            let (k, v) = kv.trim().split_once('=')?;
            (k == name).then(|| v.to_string())
        })
}

/// Loopback, private, link-local, CGNAT/Tailscale (100.64/10) and IPv6 ULA addresses.
pub fn is_local_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => {
            let o = v4.octets();
            v4.is_loopback()
                || v4.is_private()
                || v4.is_link_local()
                || (o[0] == 100 && (o[1] & 0xc0) == 64)
        }
        IpAddr::V6(v6) => {
            if let Some(v4) = v6.to_ipv4_mapped() {
                return is_local_ip(IpAddr::V4(v4));
            }
            let s = v6.segments()[0];
            v6.is_loopback() || (s & 0xfe00) == 0xfc00 || (s & 0xffc0) == 0xfe80
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::{HeaderMap, HeaderValue};

    #[test]
    fn cookie_roundtrip_and_tamper() {
        let a = Auth::new("secret-token".into(), 1);
        let c = a.make_cookie();
        assert!(a.verify_cookie(&c));
        let mut bad = c.clone();
        bad.replace_range(0..1, if c.starts_with('A') { "B" } else { "A" });
        assert!(!a.verify_cookie(&bad));
        // a different token's cookie is rejected
        assert!(!Auth::new("other".into(), 1).verify_cookie(&c));
        let expired = Auth::new("secret-token".into(), 0);
        assert!(!expired.verify_cookie(&expired.make_cookie()));
    }

    #[test]
    fn headers() {
        let a = Auth::new("tok".into(), 1);
        let mut h = HeaderMap::new();
        assert!(!a.is_authenticated(&h));
        h.insert("authorization", HeaderValue::from_static("Bearer tok"));
        assert!(a.is_authenticated(&h));
        h.insert("authorization", HeaderValue::from_static("Bearer nope"));
        assert!(!a.is_authenticated(&h));
        let cookie = format!("x=1; {COOKIE}={}", a.make_cookie());
        h.insert("cookie", HeaderValue::from_str(&cookie).unwrap());
        assert!(a.is_authenticated(&h));
    }

    #[test]
    fn rate_limit() {
        let a = Auth::new("tok".into(), 1);
        let ip: IpAddr = "10.0.0.5".parse().unwrap();
        for _ in 0..10 {
            assert!(a.login_allowed(ip));
        }
        assert!(!a.login_allowed(ip));
        assert!(a.login_allowed("10.0.0.6".parse().unwrap()));
    }

    #[test]
    fn local_networks() {
        for ok in [
            "127.0.0.1",
            "10.1.2.3",
            "192.168.4.1",
            "172.20.0.1",
            "169.254.1.1",
            "100.101.2.3",
            "::1",
            "fd00::1",
            "fe80::1",
            "::ffff:192.168.1.2",
        ] {
            assert!(is_local_ip(ok.parse().unwrap()), "{ok}");
        }
        for bad in ["8.8.8.8", "100.128.0.1", "2001:4860::1", "::ffff:1.1.1.1"] {
            assert!(!is_local_ip(bad.parse().unwrap()), "{bad}");
        }
    }

    #[test]
    fn secret_file_created_0600() {
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("sub/hub_token");
        let (s1, gen1) = load_or_create_secret(None, &f).unwrap();
        let (s2, gen2) = load_or_create_secret(None, &f).unwrap();
        assert!(gen1 && !gen2 && s1 == s2 && s1.len() >= 30);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(&f).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
    }
}
