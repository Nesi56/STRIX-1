//! Live state of every drone. The hub keeps one telemetry WebSocket open to each
//! drone and merges what it receives, so the fleet view costs the Pis nothing extra.

use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use futures_util::StreamExt;
use serde_json::{json, Map, Value};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::Message;

use crate::config::DroneConfig;

/// No message for this long → the drone is shown as offline.
const STALE: Duration = Duration::from_secs(6);

pub struct Drone {
    pub cfg: DroneConfig,
    pub token: Result<String, String>,
    pub live: RwLock<Live>,
}

#[derive(Default)]
pub struct Live {
    pub online: bool,
    pub since: Option<f64>,
    pub last_msg: Option<Instant>,
    pub error: Option<String>,
    pub version: Option<String>,
    pub tele: Map<String, Value>,
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

/// `http://host:8000/` → `ws://host:8000`
pub fn ws_base(url: &str) -> String {
    let u = url.trim_end_matches('/');
    if let Some(rest) = u.strip_prefix("https://") {
        format!("wss://{rest}")
    } else if let Some(rest) = u.strip_prefix("http://") {
        format!("ws://{rest}")
    } else {
        u.to_string()
    }
}

impl Live {
    /// Merge one message from the drone's `/ws/telemetry`.
    pub fn absorb(&mut self, msg: &Value) {
        let Some(obj) = msg.as_object() else { return };
        match obj.get("type").and_then(Value::as_str) {
            Some("hello") => {
                self.version = obj
                    .get("version")
                    .and_then(Value::as_str)
                    .map(str::to_string);
                if let Some(snap) = obj.get("snapshot").and_then(Value::as_object) {
                    self.tele = snap.clone();
                }
                if let Some(d) = obj.get("demo_mode") {
                    self.tele.insert("demo_mode".into(), d.clone());
                }
            }
            Some("telemetry") => {
                for (k, v) in obj {
                    if !matches!(k.as_str(), "type" | "seq" | "full") {
                        self.tele.insert(k.clone(), v.clone());
                    }
                }
            }
            _ => return,
        }
        self.last_msg = Some(Instant::now());
    }

    pub fn is_online(&self) -> bool {
        self.online && self.last_msg.is_some_and(|t| t.elapsed() < STALE)
    }
}

fn num(v: &Value, path: &[&str]) -> Value {
    let mut cur = v;
    for p in path {
        match cur.get(p) {
            Some(n) => cur = n,
            None => return Value::Null,
        }
    }
    if cur.is_number() || cur.is_boolean() || cur.is_string() {
        cur.clone()
    } else {
        Value::Null
    }
}

/// The small per-drone card the fleet view shows. Only real readings are passed on:
/// battery appears only when the drone reports a real battery source.
pub fn summarize(cfg: &DroneConfig, live: &Live, token_error: Option<&str>) -> Value {
    let t = Value::Object(live.tele.clone());
    let online = live.is_online();
    let mut counts: Vec<(String, u64)> = Vec::new();
    if let Some(dets) = t
        .pointer("/detections/detections")
        .and_then(Value::as_array)
    {
        for d in dets {
            if let Some(l) = d.get("label").and_then(Value::as_str) {
                match counts.iter_mut().find(|(n, _)| n == l) {
                    Some((_, c)) => *c += 1,
                    None => counts.push((l.to_string(), 1)),
                }
            }
        }
    }
    let objects: Vec<Value> = counts
        .into_iter()
        .map(|(label, n)| json!({ "label": label, "count": n }))
        .collect();
    let mem = t.pointer("/memory/objects").and_then(Value::as_array);
    let remembered = mem.map(|a| a.len()).unwrap_or(0);
    let hidden = mem
        .map(|a| {
            a.iter()
                .filter(|o| o.get("memory").and_then(Value::as_str) == Some("hidden"))
                .count()
        })
        .unwrap_or(0);
    let battery = match t.pointer("/system/power/battery") {
        Some(b)
            if b.get("available") == Some(&Value::Bool(true))
                && b.get("percent").is_some_and(Value::is_number) =>
        {
            json!({ "percent": b["percent"], "source": num(&t, &["system", "power", "source"]) })
        }
        _ => Value::Null,
    };
    let error =
        token_error
            .map(str::to_string)
            .or_else(|| if online { None } else { live.error.clone() });
    json!({
        "id": cfg.id,
        "name": cfg.display_name(),
        "color": cfg.color,
        "online": online,
        "error": error,
        "version": live.version,
        "connected_since": if online { live.since.map(Value::from).unwrap_or(Value::Null) } else { Value::Null },
        "last_seen_s": live.last_msg.map(|i| (i.elapsed().as_secs_f64() * 10.0).round() / 10.0),
        "demo_mode": num(&t, &["demo_mode"]),
        "attitude": {
            "roll": num(&t, &["imu", "latest", "orientation", "roll"]),
            "pitch": num(&t, &["imu", "latest", "orientation", "pitch"]),
            "yaw": num(&t, &["imu", "latest", "orientation", "yaw_relative"]),
            "state": num(&t, &["imu", "status", "state"]),
        },
        "environment": {
            "temperature_c": num(&t, &["environment", "latest", "temperature_c"]),
            "humidity_pct": num(&t, &["environment", "latest", "humidity_pct"]),
            "stale": num(&t, &["environment", "stale"]),
        },
        "servo": {
            "available": num(&t, &["servo", "available"]),
            "armed": num(&t, &["servo", "armed"]),
            "tilt_deg": num(&t, &["servo", "tilt_deg"]),
            "estop_latched": num(&t, &["servo", "estop_latched"]),
        },
        "system": {
            "cpu_temp_c": num(&t, &["system", "cpu_temp_c"]),
            "cpu_percent": num(&t, &["system", "cpu_percent"]),
        },
        "camera_fps": num(&t, &["camera", "measured_fps"]),
        "qwen": num(&t, &["ai", "qwen", "state"]),
        "agent": { "status": num(&t, &["agent", "status"]), "task": num(&t, &["agent", "task"]) },
        "recording": num(&t, &["recording", "active"]),
        "objects": objects,
        "memory": { "remembered": remembered, "hidden": hidden },
        "battery": battery,
    })
}

impl Drone {
    pub fn new(cfg: DroneConfig) -> Drone {
        let token = crate::config::drone_token(&cfg);
        Drone {
            cfg,
            token,
            live: RwLock::new(Live::default()),
        }
    }

    pub fn summary(&self) -> Value {
        let live = self.live.read().unwrap();
        summarize(
            &self.cfg,
            &live,
            self.token.as_ref().err().map(String::as_str),
        )
    }

    fn set_error(&self, e: String) {
        let mut l = self.live.write().unwrap();
        l.online = false;
        l.error = Some(e);
    }
}

/// Keep a telemetry connection to one drone open forever, reconnecting with backoff.
pub async fn watch(drone: Arc<Drone>) {
    let Ok(token) = drone.token.clone() else {
        return;
    };
    let url = format!("{}/ws/telemetry", ws_base(&drone.cfg.url));
    let mut backoff = 1u64;
    loop {
        match connect_once(&drone, &url, &token).await {
            Ok(()) => backoff = 1,
            Err(e) => drone.set_error(e),
        }
        drone.live.write().unwrap().online = false;
        tokio::time::sleep(Duration::from_secs(backoff)).await;
        backoff = (backoff * 2).min(10);
    }
}

async fn connect_once(drone: &Drone, url: &str, token: &str) -> Result<(), String> {
    let mut req = url
        .into_client_request()
        .map_err(|e| format!("bad url: {e}"))?;
    req.headers_mut().insert(
        "authorization",
        format!("Bearer {token}")
            .parse()
            .map_err(|_| "token has invalid characters")?,
    );
    let (mut ws, _) = tokio::time::timeout(
        Duration::from_secs(4),
        tokio_tungstenite::connect_async(req),
    )
    .await
    .map_err(|_| "not reachable (timed out)".to_string())?
    .map_err(|e| describe_ws_error(&e))?;
    {
        let mut l = drone.live.write().unwrap();
        l.online = true;
        l.since = Some(now());
        l.error = None;
    }
    tracing::info!(drone = %drone.cfg.id, "connected");
    loop {
        let msg = match tokio::time::timeout(STALE, ws.next()).await {
            Err(_) => return Err("no telemetry for 6 s".into()),
            Ok(None) => return Err("connection closed".into()),
            Ok(Some(Err(e))) => return Err(describe_ws_error(&e)),
            Ok(Some(Ok(m))) => m,
        };
        if let Message::Text(txt) = msg {
            if let Ok(v) = serde_json::from_str::<Value>(txt.as_str()) {
                drone.live.write().unwrap().absorb(&v);
            }
        }
    }
}

fn describe_ws_error(e: &tokio_tungstenite::tungstenite::Error) -> String {
    use tokio_tungstenite::tungstenite::Error as E;
    match e {
        E::Http(r) if r.status() == 401 || r.status() == 403 => "drone rejected the token".into(),
        E::Http(r) => format!("drone answered HTTP {}", r.status()),
        E::Io(io) => format!("not reachable ({})", io.kind()),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg() -> DroneConfig {
        DroneConfig {
            id: "alpha".into(),
            name: None,
            url: "http://x:8000".into(),
            token_env: Some("X".into()),
            token_file: None,
            color: None,
        }
    }

    #[test]
    fn ws_urls() {
        assert_eq!(ws_base("http://10.0.0.2:8000/"), "ws://10.0.0.2:8000");
        assert_eq!(ws_base("https://a.ts.net"), "wss://a.ts.net");
    }

    #[test]
    fn merge_and_summarize() {
        let mut l = Live {
            online: true,
            ..Default::default()
        };
        l.absorb(&json!({"type": "hello", "version": "0.1.0", "demo_mode": true, "snapshot": {
            "imu": {"latest": {"orientation": {"roll": 1.5, "pitch": -2.0, "yaw_relative": 10.0}}, "status": {"state": "synthetic"}},
            "environment": {"latest": {"temperature_c": 22.0, "humidity_pct": 40.0}, "stale": false},
            "system": {"cpu_temp_c": 61.2, "power": {"battery": {"available": false, "percent": 80}}},
            "memory": {"objects": [{"memory": "seen"}, {"memory": "hidden"}]},
        }}));
        l.absorb(&json!({"type": "telemetry", "seq": 9, "imu": {"latest": {"orientation": {"roll": 3.0}}},
            "detections": {"detections": [{"label": "cup"}, {"label": "cup"}, {"label": "sports ball"}]}}));
        let s = summarize(&cfg(), &l, None);
        assert_eq!(s["online"], true);
        assert_eq!(s["name"], "ALPHA");
        assert_eq!(s["attitude"]["roll"], 3.0);
        assert_eq!(
            s["attitude"]["pitch"],
            Value::Null,
            "partial update replaces the subtree, never invents values"
        );
        assert_eq!(s["environment"]["temperature_c"], 22.0);
        assert_eq!(s["system"]["cpu_temp_c"], 61.2);
        assert_eq!(
            s["objects"],
            json!([{"label": "cup", "count": 2}, {"label": "sports ball", "count": 1}])
        );
        assert_eq!(s["memory"], json!({"remembered": 2, "hidden": 1}));
        assert_eq!(
            s["battery"],
            Value::Null,
            "battery hidden unless the drone has a real source"
        );
        assert_eq!(s["demo_mode"], true);
    }

    #[test]
    fn battery_only_from_real_source() {
        let mut l = Live::default();
        l.absorb(&json!({"type": "hello", "snapshot": {"system": {"power": {"source": "ina219", "battery": {"available": true, "percent": 64}}}}}));
        assert_eq!(
            summarize(&cfg(), &l, None)["battery"],
            json!({"percent": 64, "source": "ina219"})
        );
    }

    #[test]
    fn offline_when_stale_or_never_connected() {
        let l = Live::default();
        let s = summarize(&cfg(), &l, Some("no token"));
        assert_eq!(s["online"], false);
        assert_eq!(s["error"], "no token");
    }
}
