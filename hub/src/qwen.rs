//! Share one Qwen (llama.cpp `llama-server` on the hub computer) with every drone.
//!
//! The relay listens on its own port for the drones only. It admits private
//! networks only, requires the shared key, and forwards just the few llama.cpp
//! routes STRIX-1 uses. Each Pi then sets `QWEN_SERVER_URL=http://<hub>:9080`,
//! `QWEN_API_KEY=<key>` and `QWEN_MANAGE_SERVER=false`, and stays cool.

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::extract::{ConnectInfo, Request, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::any;
use axum::Router;
use serde_json::json;

use crate::auth::{ct_eq, is_local_ip};
use crate::config::QwenConfig;
use crate::{api_error, AppState};

const ALLOWED: &[&str] = &["/health", "/props", "/v1/models", "/v1/chat/completions"];
/// Chat requests carry a camera frame as base64.
const MAX_BODY: usize = 16 << 20;

struct Relay {
    upstream: String,
    key: String,
    http: reqwest::Client,
}

pub fn allowed_path(path: &str) -> bool {
    ALLOWED.contains(&path)
}

fn bearer(req: &Request) -> &str {
    req.headers()
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| {
            v.strip_prefix("Bearer ")
                .or_else(|| v.strip_prefix("bearer "))
        })
        .unwrap_or("")
}

async fn relay(
    State(r): State<Arc<Relay>>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    req: Request,
) -> Response {
    if !is_local_ip(addr.ip()) {
        return api_error(
            StatusCode::FORBIDDEN,
            "forbidden_network",
            "client network not allowed",
        );
    }
    if !ct_eq(bearer(&req).trim(), &r.key) {
        return api_error(
            StatusCode::UNAUTHORIZED,
            "unauthorized",
            "wrong or missing Qwen key",
        );
    }
    let path = req.uri().path().to_string();
    if !allowed_path(&path) {
        return api_error(
            StatusCode::NOT_FOUND,
            "not_found",
            "not available through the hub",
        );
    }
    let method = req.method().clone();
    let ctype = req.headers().get("content-type").cloned();
    let Ok(body) = axum::body::to_bytes(req.into_body(), MAX_BODY).await else {
        return api_error(
            StatusCode::PAYLOAD_TOO_LARGE,
            "too_large",
            "request too large",
        );
    };
    let mut up = r
        .http
        .request(
            method,
            format!("{}{}", r.upstream.trim_end_matches('/'), path),
        )
        .body(body);
    if let Some(c) = ctype {
        up = up.header("content-type", c);
    }
    match up.send().await {
        Ok(resp) => {
            let mut out = Response::builder().status(resp.status());
            if let Some(c) = resp.headers().get("content-type") {
                out = out.header("content-type", c.clone());
            }
            out.body(Body::from_stream(resp.bytes_stream()))
                .unwrap_or_else(|_| StatusCode::BAD_GATEWAY.into_response())
        }
        Err(_) => api_error(
            StatusCode::BAD_GATEWAY,
            "qwen_unreachable",
            "llama-server on the hub is not running",
        ),
    }
}

pub async fn serve(q: QwenConfig, key: String, http: reqwest::Client) {
    let relay_state = Arc::new(Relay {
        upstream: q.upstream.clone(),
        key,
        http,
    });
    let app = Router::new()
        .route("/{*path}", any(relay))
        .with_state(relay_state);
    match tokio::net::TcpListener::bind(&q.listen).await {
        Ok(l) => {
            tracing::info!("Qwen relay for drones on {} → {}", q.listen, q.upstream);
            if let Err(e) =
                axum::serve(l, app.into_make_service_with_connect_info::<SocketAddr>()).await
            {
                tracing::error!("Qwen relay stopped: {e}");
            }
        }
        Err(e) => tracing::error!("Qwen relay cannot listen on {}: {e}", q.listen),
    }
}

/// Keep the fleet view's Qwen line current.
pub async fn monitor(st: Arc<AppState>, upstream: String) {
    let url = format!("{}/health", upstream.trim_end_matches('/'));
    loop {
        let state = match st
            .http
            .get(&url)
            .timeout(Duration::from_secs(2))
            .send()
            .await
        {
            Ok(r) if r.status().is_success() => "ready",
            Ok(r) if r.status() == StatusCode::SERVICE_UNAVAILABLE => "loading",
            Ok(_) => "error",
            Err(_) => "not running",
        };
        let listen = st.cfg.qwen.as_ref().map(|q| q.listen.clone());
        *st.qwen_status.write().unwrap() = json!({ "state": state, "relay": listen });
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn only_llama_routes() {
        assert!(super::allowed_path("/v1/chat/completions"));
        assert!(super::allowed_path("/health"));
        assert!(!super::allowed_path("/slots"));
        assert!(!super::allowed_path("/v1/chat/completions/../x"));
    }
}
