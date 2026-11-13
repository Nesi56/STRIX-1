//! `hub.toml`: where the hub listens and which drones it shows.
//!
//! Drone access tokens are never written in this file: each drone names an
//! environment variable (`token_env`) or a file (`token_file`) that holds it.

use std::path::{Path, PathBuf};

use serde::Deserialize;

pub const MAX_DRONES: usize = 8;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    /// Dashboard address. Loopback by default: only this computer can open it.
    #[serde(default = "default_listen")]
    pub listen: String,
    /// Admit browsers from public IP addresses (off by default; private, link-local
    /// and Tailscale addresses are always admitted).
    #[serde(default)]
    pub allow_public_clients: bool,
    /// Hub state: the generated access token and keys.
    #[serde(default = "default_data_dir")]
    pub data_dir: PathBuf,
    /// Built dashboard (`frontend/dist`). Optional: without it the hub serves only the API.
    #[serde(default)]
    pub ui_dir: Option<PathBuf>,
    #[serde(default = "default_ttl")]
    pub session_ttl_hours: u64,
    #[serde(default, rename = "drone")]
    pub drones: Vec<DroneConfig>,
    #[serde(default)]
    pub qwen: Option<QwenConfig>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DroneConfig {
    /// Short id used in URLs: lowercase letters, digits and dashes.
    pub id: String,
    #[serde(default)]
    pub name: Option<String>,
    /// Base URL of the drone's STRIX-1 service, e.g. `http://strix-alpha.local:8000`.
    pub url: String,
    #[serde(default)]
    pub token_env: Option<String>,
    #[serde(default)]
    pub token_file: Option<PathBuf>,
    /// Accent colour for the fleet view, e.g. `#5fd4ff`.
    #[serde(default)]
    pub color: Option<String>,
}

/// Share one Qwen (llama.cpp `llama-server`) running on the hub computer with every drone.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QwenConfig {
    /// Address drones use to reach the relay (LAN-facing; private networks only).
    #[serde(default = "default_qwen_listen")]
    pub listen: String,
    /// The local llama-server, normally bound to loopback.
    #[serde(default = "default_qwen_upstream")]
    pub upstream: String,
    /// Environment variable holding the key drones send (`QWEN_API_KEY` on each Pi).
    /// When unset and no file exists, a key is generated in `<data_dir>/qwen_key`.
    #[serde(default)]
    pub key_env: Option<String>,
}

fn default_listen() -> String {
    "127.0.0.1:9000".into()
}
fn default_data_dir() -> PathBuf {
    PathBuf::from("data")
}
fn default_ttl() -> u64 {
    12
}
fn default_qwen_listen() -> String {
    "0.0.0.0:9080".into()
}
fn default_qwen_upstream() -> String {
    "http://127.0.0.1:8080".into()
}

impl DroneConfig {
    pub fn display_name(&self) -> String {
        self.name.clone().unwrap_or_else(|| self.id.to_uppercase())
    }
}

pub fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 32
        && id
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        && !id.starts_with('-')
}

fn valid_color(c: &str) -> bool {
    c.len() == 7 && c.starts_with('#') && c[1..].bytes().all(|b| b.is_ascii_hexdigit())
}

impl Config {
    pub fn parse(text: &str, base_dir: &Path) -> Result<Config, String> {
        let mut cfg: Config = toml::from_str(text).map_err(|e| format!("hub.toml: {e}"))?;
        cfg.resolve_paths(base_dir);
        cfg.validate()?;
        Ok(cfg)
    }

    pub fn load(path: &Path) -> Result<Config, String> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        let base = path.parent().map(Path::to_path_buf).unwrap_or_default();
        Config::parse(&text, &base)
    }

    fn resolve_paths(&mut self, base: &Path) {
        let fix = |p: &mut PathBuf| {
            if p.is_relative() {
                *p = base.join(&*p);
            }
        };
        fix(&mut self.data_dir);
        if let Some(ui) = self.ui_dir.as_mut() {
            fix(ui);
        }
        for d in &mut self.drones {
            if let Some(f) = d.token_file.as_mut() {
                fix(f);
            }
        }
    }

    fn validate(&self) -> Result<(), String> {
        if self.drones.len() > MAX_DRONES {
            return Err(format!("at most {MAX_DRONES} drones are supported"));
        }
        let mut seen = std::collections::HashSet::new();
        for d in &self.drones {
            if !valid_id(&d.id) {
                return Err(format!(
                    "drone id {:?}: use 1-32 lowercase letters, digits and dashes",
                    d.id
                ));
            }
            if !seen.insert(d.id.clone()) {
                return Err(format!("drone id {:?} is used twice", d.id));
            }
            if !(d.url.starts_with("http://") || d.url.starts_with("https://")) {
                return Err(format!(
                    "drone {:?}: url must start with http:// or https://",
                    d.id
                ));
            }
            if d.url.contains('@') {
                return Err(format!(
                    "drone {:?}: put credentials in token_env/token_file, not in the url",
                    d.id
                ));
            }
            if d.token_env.is_none() && d.token_file.is_none() {
                return Err(format!("drone {:?}: set token_env or token_file", d.id));
            }
            if let Some(c) = &d.color {
                if !valid_color(c) {
                    return Err(format!("drone {:?}: color must look like #5fd4ff", d.id));
                }
            }
        }
        if let Some(q) = &self.qwen {
            if !(q.upstream.starts_with("http://") || q.upstream.starts_with("https://")) {
                return Err("qwen.upstream must start with http:// or https://".into());
            }
        }
        Ok(())
    }
}

/// Read a drone's access token from its environment variable or file. Never logged.
pub fn drone_token(d: &DroneConfig) -> Result<String, String> {
    if let Some(var) = &d.token_env {
        if let Ok(v) = std::env::var(var) {
            if !v.trim().is_empty() {
                return Ok(v.trim().to_string());
            }
        }
    }
    if let Some(f) = &d.token_file {
        match std::fs::read_to_string(f) {
            Ok(v) if !v.trim().is_empty() => return Ok(v.trim().to_string()),
            Ok(_) => return Err(format!("{} is empty", f.display())),
            Err(e) => return Err(format!("cannot read {}: {e}", f.display())),
        }
    }
    Err(format!(
        "no token: set the environment variable {}",
        d.token_env.as_deref().unwrap_or("named in token_env")
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    const OK: &str = r##"
listen = "127.0.0.1:9000"
ui_dir = "../frontend/dist"
[[drone]]
id = "alpha"
url = "http://10.0.0.21:8000"
token_env = "STRIX_ALPHA_TOKEN"
color = "#5fd4ff"
[[drone]]
id = "bravo"
name = "Bravo"
url = "http://10.0.0.22:8000"
token_file = "secrets/bravo"
"##;

    #[test]
    fn parses_and_resolves_paths() {
        let c = Config::parse(OK, Path::new("/etc/strix")).unwrap();
        assert_eq!(c.drones.len(), 2);
        assert_eq!(c.drones[0].display_name(), "ALPHA");
        assert_eq!(
            c.drones[1].token_file.as_deref(),
            Some(Path::new("/etc/strix/secrets/bravo"))
        );
        assert_eq!(
            c.ui_dir.as_deref(),
            Some(Path::new("/etc/strix/../frontend/dist"))
        );
        assert_eq!(c.data_dir, PathBuf::from("/etc/strix/data"));
        assert!(c.qwen.is_none());
    }

    #[test]
    fn rejects_bad_drones() {
        let dup = OK.replace("bravo", "alpha");
        assert!(Config::parse(&dup, Path::new("."))
            .unwrap_err()
            .contains("twice"));
        let bad_id = OK.replace("\"alpha\"", "\"Alpha One\"");
        assert!(Config::parse(&bad_id, Path::new(".")).is_err());
        let creds = OK.replace("http://10.0.0.21", "http://user:pw@10.0.0.21");
        assert!(Config::parse(&creds, Path::new("."))
            .unwrap_err()
            .contains("credentials"));
        let no_token = OK.replace("token_env = \"STRIX_ALPHA_TOKEN\"", "");
        assert!(Config::parse(&no_token, Path::new("."))
            .unwrap_err()
            .contains("token"));
        let unknown = format!("{OK}\nsecret = \"x\"");
        assert!(Config::parse(&unknown, Path::new(".")).is_err());
    }

    #[test]
    fn ids() {
        assert!(valid_id("alpha-2"));
        assert!(!valid_id("-x"));
        assert!(!valid_id("../x"));
        assert!(!valid_id(""));
    }
}
