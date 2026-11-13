//! End-to-end tests: a fake drone and the hub, both on loopback ports.

use std::net::SocketAddr;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, RwLock};
use std::time::Duration;

use axum::extract::ws::{Message, WebSocketUpgrade};
use axum::http::HeaderMap;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};

use super::*;

const DRONE_TOKEN: &str = "drone-secret";
const HUB_TOKEN: &str = "hub-secret";

fn authed(h: &HeaderMap) -> bool {
    h.get("authorization").and_then(|v| v.to_str().ok()) == Some(&format!("Bearer {DRONE_TOKEN}"))
}

async fn fake_drone(estops: Arc<AtomicUsize>) -> SocketAddr {
    let e = estops.clone();
    let app = Router::new()
        .route(
            "/api/servo",
            get(|h: HeaderMap| async move {
                if !authed(&h) {
                    return (axum::http::StatusCode::UNAUTHORIZED, "no").into_response();
                }
                let leaked = h.contains_key("cookie");
                ([("set-cookie", "strix_session=drone"), ("x-secret", "1")], Json(json!({"armed": false, "cookie_leaked": leaked})))
                    .into_response()
            }),
        )
        .route("/api/servo/nudge", post(|h: HeaderMap, body: String| async move { Json(json!({"ok": authed(&h), "body": body})) }))
        .route(
            "/api/estop",
            post(move |h: HeaderMap| {
                let e = e.clone();
                async move {
                    if authed(&h) {
                        e.fetch_add(1, Ordering::SeqCst);
                    }
                    Json(json!({"stopped": true}))
                }
            }),
        )
        .route(
            "/ws/telemetry",
            get(|h: HeaderMap, ws: WebSocketUpgrade| async move {
                if !authed(&h) {
                    return axum::http::StatusCode::UNAUTHORIZED.into_response();
                }
                ws.on_upgrade(|mut s| async move {
                    let hello = json!({"type": "hello", "version": "0.1.0", "demo_mode": true, "snapshot": {
                        "imu": {"latest": {"orientation": {"roll": 2.0, "pitch": 1.0, "yaw_relative": 30.0}}},
                        "environment": {"latest": {"temperature_c": 23.0, "humidity_pct": 41.0}},
                        "detections": {"detections": [{"label": "cup"}]}}});
                    let _ = s.send(Message::Text(hello.to_string().into())).await;
                    loop {
                        tokio::select! {
                            m = s.next() => match m {
                                Some(Ok(Message::Text(t))) => { let _ = s.send(Message::Text(format!("echo:{}", t.as_str()).into())).await; }
                                None | Some(Err(_)) => break,
                                _ => {}
                            },
                            _ = tokio::time::sleep(Duration::from_millis(200)) => {
                                let t = json!({"type": "telemetry", "seq": 1, "imu": {"latest": {"orientation": {"roll": 5.0, "pitch": 1.0, "yaw_relative": 31.0}}}});
                                if s.send(Message::Text(t.to_string().into())).await.is_err() { break; }
                            }
                        }
                    }
                })
                .into_response()
            }),
        );
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(l, app).await.unwrap() });
    addr
}

async fn hub(drone_addr: SocketAddr, env_var: &str) -> SocketAddr {
    // Each test uses its own variable name, so tests running in parallel do not interfere.
    std::env::set_var(env_var, DRONE_TOKEN);
    let toml = format!(
        r#"
[[drone]]
id = "alpha"
url = "http://{drone_addr}"
token_env = "{env_var}"
[[drone]]
id = "ghost"
url = "http://127.0.0.1:9"
token_env = "STRIX_TEST_UNSET_TOKEN_VAR"
"#
    );
    let cfg = Config::parse(&toml, std::path::Path::new("/nonexistent")).unwrap();
    let drones: Vec<Arc<Drone>> = cfg
        .drones
        .iter()
        .cloned()
        .map(|d| Arc::new(Drone::new(d)))
        .collect();
    for d in &drones {
        tokio::spawn(fleet::watch(d.clone()));
    }
    let st = Arc::new(AppState {
        auth: Auth::new(HUB_TOKEN.into(), 1),
        cfg,
        drones,
        http: reqwest::Client::new(),
        qwen_status: RwLock::new(Value::Null),
        pin: pin::PinStore::new(Some(pin::hash_pin("482915", 1_000))),
    });
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(
            l,
            router(st).into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await
        .unwrap()
    });
    addr
}

#[tokio::test]
async fn login_fleet_proxy_estop() {
    let estops = Arc::new(AtomicUsize::new(0));
    let d = fake_drone(estops.clone()).await;
    let h = hub(d, "STRIX_TEST_TOKEN_A").await;
    let c = reqwest::Client::new();
    let base = format!("http://{h}");

    // public
    let info: Value = c
        .get(format!("{base}/api/hub/info"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(info["hub"], true);
    // protected without login
    assert_eq!(
        c.get(format!("{base}/api/hub/fleet"))
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
    assert_eq!(
        c.get(format!("{base}/d/alpha/api/servo"))
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
    // bad login, then good login → cookie
    assert_eq!(
        c.post(format!("{base}/api/auth/login"))
            .json(&json!({"token": "nope"}))
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
    let r = c
        .post(format!("{base}/api/auth/login"))
        .json(&json!({"token": HUB_TOKEN}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    let set = r.headers()["set-cookie"].to_str().unwrap().to_string();
    assert!(set.contains("HttpOnly") && set.contains("SameSite=Strict"));
    let cookie = set.split(';').next().unwrap().to_string();

    // proxy GET: drone token injected, hub cookie not forwarded, drone cookie not returned
    let r = c
        .get(format!("{base}/d/alpha/api/servo"))
        .header("cookie", &cookie)
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    assert!(r.headers().get("set-cookie").is_none() && r.headers().get("x-secret").is_none());
    let v: Value = r.json().await.unwrap();
    assert_eq!(v, json!({"armed": false, "cookie_leaked": false}));
    // proxy POST with body
    let v: Value = c
        .post(format!("{base}/d/alpha/api/servo/nudge"))
        .header("cookie", &cookie)
        .json(&json!({"up_deg": 5}))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(v["ok"], true);
    assert_eq!(v["body"], "{\"up_deg\":5}");
    // refused paths
    assert_eq!(
        c.post(format!("{base}/d/alpha/api/auth/login"))
            .header("cookie", &cookie)
            .send()
            .await
            .unwrap()
            .status(),
        404
    );
    assert_eq!(
        c.get(format!("{base}/d/nobody/api/servo"))
            .header("cookie", &cookie)
            .send()
            .await
            .unwrap()
            .status(),
        404
    );
    assert_eq!(
        c.get(format!("{base}/d/ghost/api/servo"))
            .header("cookie", &cookie)
            .send()
            .await
            .unwrap()
            .status(),
        503
    );

    // fleet: alpha online with merged telemetry, ghost reports its config problem
    let mut fleet = Value::Null;
    for _ in 0..30 {
        fleet = c
            .get(format!("{base}/api/hub/fleet"))
            .header("cookie", &cookie)
            .send()
            .await
            .unwrap()
            .json()
            .await
            .unwrap();
        if fleet["drones"][0]["online"] == true && fleet["drones"][0]["attitude"]["roll"] == 5.0 {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    let a = &fleet["drones"][0];
    assert_eq!(a["online"], true, "{fleet}");
    assert_eq!(a["attitude"]["roll"], 5.0);
    assert_eq!(a["environment"]["temperature_c"], 23.0);
    assert_eq!(a["objects"], json!([{"label": "cup", "count": 1}]));
    let g = &fleet["drones"][1];
    assert_eq!(g["online"], false);
    assert!(g["error"]
        .as_str()
        .unwrap()
        .contains("STRIX_TEST_UNSET_TOKEN_VAR"));
    assert!(
        !fleet.to_string().contains(DRONE_TOKEN),
        "drone token never leaves the hub"
    );

    // fleet e-stop reaches the drone
    let v: Value = c
        .post(format!("{base}/api/hub/estop"))
        .header("cookie", &cookie)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(v["results"][0]["ok"], true);
    assert_eq!(v["results"][1]["ok"], false);
    assert_eq!(estops.load(Ordering::SeqCst), 1);

    // unknown api path → JSON 404, not the SPA
    assert_eq!(
        c.get(format!("{base}/api/nope"))
            .send()
            .await
            .unwrap()
            .status(),
        404
    );
}

#[tokio::test]
async fn websocket_passthrough() {
    let d = fake_drone(Arc::new(AtomicUsize::new(0))).await;
    let h = hub(d, "STRIX_TEST_TOKEN_B").await;
    let mut req = format!("ws://{h}/d/alpha/ws/telemetry")
        .into_client_request()
        .unwrap();
    // unauthenticated → refused
    assert!(tokio_tungstenite::connect_async(req.clone()).await.is_err());
    req.headers_mut().insert(
        "authorization",
        format!("Bearer {HUB_TOKEN}").parse().unwrap(),
    );
    // cross-site origin → refused
    let mut evil = req.clone();
    evil.headers_mut()
        .insert("origin", "http://evil.example".parse().unwrap());
    assert!(tokio_tungstenite::connect_async(evil).await.is_err());

    let (mut ws, _) = tokio_tungstenite::connect_async(req).await.unwrap();
    let first = ws.next().await.unwrap().unwrap();
    let v: Value = serde_json::from_str(first.to_text().unwrap()).unwrap();
    assert_eq!(v["type"], "hello");
    ws.send(tokio_tungstenite::tungstenite::Message::Text(
        "{\"type\":\"ping\"}".into(),
    ))
    .await
    .unwrap();
    let mut echoed = false;
    for _ in 0..20 {
        let m = ws.next().await.unwrap().unwrap();
        if m.to_text().unwrap().starts_with("echo:") {
            echoed = true;
            break;
        }
    }
    assert!(echoed, "client → drone messages are forwarded");
}

use tokio_tungstenite::tungstenite::client::IntoClientRequest;

#[tokio::test]
async fn pin_login() {
    let d = fake_drone(Arc::new(AtomicUsize::new(0))).await;
    let h = hub(d, "STRIX_TEST_TOKEN_C").await;
    let c = reqwest::Client::new();
    let base = format!("http://{h}");
    let s: Value = c
        .get(format!("{base}/api/auth/status"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(s["pin_enabled"], true);
    let r = c
        .post(format!("{base}/api/auth/login"))
        .json(&json!({"pin": "111112"}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 401);
    let v: Value = r.json().await.unwrap();
    assert_eq!(v["error"]["tries_left"], 4);
    let r = c
        .post(format!("{base}/api/auth/login"))
        .json(&json!({"pin": "482915"}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    let cookie = r.headers()["set-cookie"]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_string();
    assert_eq!(
        c.get(format!("{base}/api/hub/fleet"))
            .header("cookie", cookie)
            .send()
            .await
            .unwrap()
            .status(),
        200
    );
    // the long token still works as a recovery login
    let r = c
        .post(format!("{base}/api/auth/login"))
        .json(&json!({"token": HUB_TOKEN}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
}
