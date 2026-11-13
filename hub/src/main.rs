//! STRIX-1 hub: one self-hosted dashboard for several STRIX-1 drones.
//!
//! * Runs on the laptop you bring to the field; each drone keeps its own service.
//! * Advisory only: the hub forwards the drones' existing commands (camera servo,
//!   memory, chat) and never sends flight, motor or navigation commands.
//! * Loopback by default; private networks only unless explicitly allowed.
//!
//! Usage: `strix-hub [hub.toml]`, `strix-hub token [hub.toml]`, `strix-hub qwen-key [hub.toml]`

mod auth;
mod config;
mod fleet;
mod proxy;
mod qwen;

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, RwLock};
use std::time::Duration;

use axum::extract::{ConnectInfo, Request, State};
use axum::http::{header, HeaderValue, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::{any, get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use tower::ServiceExt;
use tower_http::services::{ServeDir, ServeFile};

use crate::auth::Auth;
use crate::config::Config;
use crate::fleet::Drone;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub struct AppState {
    pub cfg: Config,
    pub auth: Auth,
    pub drones: Vec<Arc<Drone>>,
    pub http: reqwest::Client,
    pub qwen_status: RwLock<Value>,
}

pub fn api_error(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "error": { "code": code, "message": message } })),
    )
        .into_response()
}

// ------------------------------------------------------------------ handlers
async fn health(State(st): State<Arc<AppState>>) -> Json<Value> {
    Json(
        json!({ "status": "ok", "service": "strix-hub", "version": VERSION, "drones": st.drones.len() }),
    )
}

/// Public: lets the dashboard know it is talking to a hub (not directly to a drone).
async fn hub_info() -> Json<Value> {
    Json(json!({ "hub": true, "version": VERSION }))
}

async fn auth_status(State(st): State<Arc<AppState>>, req: Request) -> Json<Value> {
    Json(json!({ "auth_enabled": true, "authenticated": st.auth.is_authenticated(req.headers()) }))
}

#[derive(Deserialize)]
struct Login {
    token: String,
}

async fn login(
    State(st): State<Arc<AppState>>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    req: Request,
) -> Response {
    if !st.auth.login_allowed(addr.ip()) {
        return api_error(
            StatusCode::TOO_MANY_REQUESTS,
            "rate_limited",
            "too many login attempts; wait a minute",
        );
    }
    let secure = req
        .headers()
        .get("x-forwarded-proto")
        .is_some_and(|v| v == "https");
    let Ok(body) = axum::body::to_bytes(req.into_body(), 4096).await else {
        return api_error(StatusCode::BAD_REQUEST, "bad_request", "invalid body");
    };
    let Ok(l) = serde_json::from_slice::<Login>(&body) else {
        return api_error(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "send {\"token\": \"...\"}",
        );
    };
    if !st.auth.check_token(&l.token) {
        return api_error(
            StatusCode::UNAUTHORIZED,
            "invalid_token",
            "invalid access token",
        );
    }
    let cookie = format!(
        "{}={}; Max-Age={}; Path=/; HttpOnly; SameSite=Strict{}",
        auth::COOKIE,
        st.auth.make_cookie(),
        st.auth.ttl_s,
        if secure { "; Secure" } else { "" }
    );
    (
        [(header::SET_COOKIE, cookie)],
        Json(json!({ "authenticated": true })),
    )
        .into_response()
}

async fn logout() -> Response {
    let cookie = format!(
        "{}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict",
        auth::COOKIE
    );
    (
        [(header::SET_COOKIE, cookie)],
        Json(json!({ "authenticated": false })),
    )
        .into_response()
}

async fn fleet(State(st): State<Arc<AppState>>) -> Json<Value> {
    let drones: Vec<Value> = st.drones.iter().map(|d| d.summary()).collect();
    let qwen = st.qwen_status.read().unwrap().clone();
    Json(json!({ "version": VERSION, "drones": drones, "qwen": qwen }))
}

/// Stop every drone's camera servo and AI task at once (the drones' own e-stop).
async fn estop_all(State(st): State<Arc<AppState>>) -> Json<Value> {
    let calls = st.drones.iter().map(|d| {
        let st = st.clone();
        let d = d.clone();
        async move {
            let Ok(token) = d.token.clone() else {
                return json!({ "id": d.cfg.id, "ok": false, "error": "no token configured" });
            };
            let url = format!("{}/api/estop", d.cfg.url.trim_end_matches('/'));
            let r = st.http.post(url).bearer_auth(token).json(&json!({})).timeout(Duration::from_secs(3)).send().await;
            match r {
                Ok(r) if r.status().is_success() => json!({ "id": d.cfg.id, "ok": true }),
                Ok(r) => json!({ "id": d.cfg.id, "ok": false, "error": format!("HTTP {}", r.status().as_u16()) }),
                Err(_) => json!({ "id": d.cfg.id, "ok": false, "error": "not reachable" }),
            }
        }
    });
    let results = futures_util::future::join_all(calls).await;
    tracing::warn!("fleet e-stop sent to {} drone(s)", results.len());
    Json(json!({ "results": results }))
}

const NOT_BUILT: &str = r#"<!doctype html><meta charset=utf-8><title>STRIX-1 Hub</title>
<body style="background:#050b16;color:#cfe8ff;font-family:system-ui;padding:2rem">
<h1>STRIX-1 hub is running</h1><p>The dashboard has not been built yet. Run
<code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code> and set <code>ui_dir</code> in hub.toml.</p>"#;

async fn fallback(State(st): State<Arc<AppState>>, req: Request) -> Response {
    let path = req.uri().path();
    if path.starts_with("/api/") || path.starts_with("/d/") || path.starts_with("/ws/") {
        return api_error(StatusCode::NOT_FOUND, "not_found", "not found");
    }
    match &st.cfg.ui_dir {
        Some(dir) if dir.join("index.html").is_file() => {
            let svc = ServeDir::new(dir).fallback(ServeFile::new(dir.join("index.html")));
            match svc.oneshot(req).await {
                Ok(r) => r.into_response(),
                Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
            }
        }
        _ => Html(NOT_BUILT).into_response(),
    }
}

// ------------------------------------------------------------------ middleware
async fn require_auth(State(st): State<Arc<AppState>>, req: Request, next: Next) -> Response {
    if st.auth.is_authenticated(req.headers()) {
        next.run(req).await
    } else {
        api_error(
            StatusCode::UNAUTHORIZED,
            "unauthorized",
            "authentication required",
        )
    }
}

async fn network_guard(
    State(st): State<Arc<AppState>>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    req: Request,
    next: Next,
) -> Response {
    if !st.cfg.allow_public_clients && !auth::is_local_ip(addr.ip()) {
        tracing::warn!(client = %addr.ip(), "rejected non-local client");
        return api_error(
            StatusCode::FORBIDDEN,
            "forbidden_network",
            "client network not allowed",
        );
    }
    let mut res = next.run(req).await;
    let h = res.headers_mut();
    h.insert(
        "x-content-type-options",
        HeaderValue::from_static("nosniff"),
    );
    h.insert("x-frame-options", HeaderValue::from_static("DENY"));
    h.insert("referrer-policy", HeaderValue::from_static("no-referrer"));
    res
}

pub fn router(st: Arc<AppState>) -> Router {
    let protected = Router::new()
        .route("/api/hub/fleet", get(fleet))
        .route("/api/hub/estop", post(estop_all))
        .route("/d/{id}/api/{*rest}", any(proxy::http))
        .route("/d/{id}/ws/telemetry", get(proxy::ws))
        .route_layer(middleware::from_fn_with_state(st.clone(), require_auth));
    Router::new()
        .route("/api/health", get(health))
        .route("/api/hub/info", get(hub_info))
        .route("/api/auth/status", get(auth_status))
        .route("/api/auth/login", post(login))
        .route("/api/auth/logout", post(logout))
        .merge(protected)
        .fallback(fallback)
        .layer(middleware::from_fn_with_state(st.clone(), network_guard))
        .with_state(st)
}

// ------------------------------------------------------------------ startup
fn config_path(args: &[String]) -> PathBuf {
    args.iter()
        .find(|a| a.ends_with(".toml"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("hub.toml"))
}

fn fail(msg: impl std::fmt::Display) -> ! {
    eprintln!("strix-hub: {msg}");
    std::process::exit(2)
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "strix_hub=info".into()),
        )
        .init();
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "-h" || a == "--help") {
        println!("strix-hub {VERSION}\n\n  strix-hub [hub.toml]            run the hub\n  strix-hub token [hub.toml]      print the dashboard access token\n  strix-hub qwen-key [hub.toml]   print the key drones use for the shared Qwen");
        return;
    }
    let path = config_path(&args);
    let cfg = Config::load(&path).unwrap_or_else(|e| fail(e));
    let token_file = cfg.data_dir.join("hub_token");
    let (token, generated) = auth::load_or_create_secret(Some("STRIX_HUB_TOKEN"), &token_file)
        .unwrap_or_else(|e| fail(format!("hub token: {e}")));
    let qwen_key = cfg.qwen.as_ref().map(|q| {
        let file = cfg.data_dir.join("qwen_key");
        auth::load_or_create_secret(q.key_env.as_deref().or(Some("STRIX_QWEN_KEY")), &file)
            .unwrap_or_else(|e| fail(format!("qwen key: {e}")))
            .0
    });
    match args.first().map(String::as_str) {
        Some("token") => {
            println!("{token}");
            return;
        }
        Some("qwen-key") => {
            println!(
                "{}",
                qwen_key.unwrap_or_else(|| fail("no [qwen] section in hub.toml"))
            );
            return;
        }
        _ => {}
    }
    if generated {
        tracing::warn!(
            "created a dashboard access token in {} (show it with `strix-hub token`)",
            token_file.display()
        );
    }

    let http = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(3))
        .pool_idle_timeout(Duration::from_secs(30))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .unwrap_or_else(|e| fail(e));
    let drones: Vec<Arc<Drone>> = cfg
        .drones
        .iter()
        .cloned()
        .map(|d| Arc::new(Drone::new(d)))
        .collect();
    for d in &drones {
        match &d.token {
            Ok(_) => tracing::info!(drone = %d.cfg.id, url = %d.cfg.url, "drone configured"),
            Err(e) => tracing::error!(drone = %d.cfg.id, "drone has no usable token: {e}"),
        }
        tokio::spawn(fleet::watch(d.clone()));
    }
    if drones.is_empty() {
        tracing::warn!("no [[drone]] entries in {}", path.display());
    }
    let listen = cfg.listen.clone();
    let qwen_cfg = cfg.qwen.clone();
    let st = Arc::new(AppState {
        auth: Auth::new(token, cfg.session_ttl_hours),
        cfg,
        drones,
        http: http.clone(),
        qwen_status: RwLock::new(Value::Null),
    });

    if let (Some(q), Some(key)) = (qwen_cfg, qwen_key) {
        tokio::spawn(qwen::monitor(st.clone(), q.upstream.clone()));
        tokio::spawn(qwen::serve(q, key, http));
    }

    let listener = tokio::net::TcpListener::bind(&listen)
        .await
        .unwrap_or_else(|e| fail(format!("cannot listen on {listen}: {e}")));
    tracing::info!("STRIX-1 hub {VERSION} → http://{listen}");
    axum::serve(
        listener,
        router(st).into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(async {
        let _ = tokio::signal::ctrl_c().await;
    })
    .await
    .unwrap_or_else(|e| fail(e));
}

#[cfg(test)]
mod tests;
