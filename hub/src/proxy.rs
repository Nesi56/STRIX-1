//! Forwarding to a drone: `/d/<id>/api/...` → `<drone>/api/...` and
//! `/d/<id>/ws/telemetry` → `<drone>/ws/telemetry`, with the drone's token added
//! by the hub. The browser never sees a drone token, and drone cookies never reach it.

use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::extract::ws::{Message as AMsg, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, Request, State};
use axum::http::{HeaderMap, HeaderName, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use futures_util::{SinkExt, StreamExt};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::Message as TMsg;

use crate::fleet::{ws_base, Drone};
use crate::{api_error, AppState};

/// Largest request body forwarded to a drone (JSON commands; nothing uploads media).
pub const MAX_BODY: usize = 1 << 20;

const REQ_HEADERS: &[&str] = &[
    "content-type",
    "accept",
    "range",
    "if-none-match",
    "if-modified-since",
];
const RESP_HEADERS: &[&str] = &[
    "content-type",
    "content-length",
    "content-disposition",
    "content-range",
    "accept-ranges",
    "cache-control",
    "etag",
    "last-modified",
    "x-accel-buffering",
];

/// The part of `/d/<id>/api/...` to forward, or why it is refused.
pub fn upstream_path(raw_path: &str, id: &str) -> Result<String, (StatusCode, &'static str)> {
    let rest = raw_path
        .strip_prefix("/d/")
        .and_then(|r| r.strip_prefix(id))
        .unwrap_or("");
    if !rest.starts_with("/api/") && !rest.starts_with("/ws/") {
        return Err((StatusCode::NOT_FOUND, "not found"));
    }
    let lower = rest.to_ascii_lowercase();
    if rest.split('/').any(|s| s == ".." || s == ".")
        || lower.contains("%2e%2e")
        || lower.contains("%2f")
        || rest.contains('\\')
    {
        return Err((StatusCode::BAD_REQUEST, "invalid path"));
    }
    // The hub owns login; a drone's own login and docs are not reachable through it.
    if rest.starts_with("/api/auth/")
        || rest.starts_with("/api/docs")
        || rest.starts_with("/api/openapi")
    {
        return Err((StatusCode::NOT_FOUND, "not available through the hub"));
    }
    Ok(rest.to_string())
}

#[allow(clippy::result_large_err)]
pub fn find(st: &AppState, id: &str) -> Result<(Arc<Drone>, String), Response> {
    let Some(d) = st.drones.iter().find(|d| d.cfg.id == id) else {
        return Err(api_error(
            StatusCode::NOT_FOUND,
            "unknown_drone",
            "no drone with that id",
        ));
    };
    match &d.token {
        Ok(t) => Ok((d.clone(), t.clone())),
        Err(_) => Err(api_error(
            StatusCode::SERVICE_UNAVAILABLE,
            "drone_not_configured",
            "the hub has no token for this drone",
        )),
    }
}

pub async fn http(
    State(st): State<Arc<AppState>>,
    Path((id, _rest)): Path<(String, String)>,
    req: Request,
) -> Response {
    let (drone, token) = match find(&st, &id) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let path = match upstream_path(req.uri().path(), &id) {
        Ok(p) if p.starts_with("/api/") => p,
        Ok(_) => return api_error(StatusCode::NOT_FOUND, "not_found", "not found"),
        Err((s, m)) => return api_error(s, "bad_path", m),
    };
    let mut url = format!("{}{}", drone.cfg.url.trim_end_matches('/'), path);
    if let Some(q) = req.uri().query() {
        url.push('?');
        url.push_str(q);
    }
    let method = req.method().clone();
    let mut headers = HeaderMap::new();
    for name in REQ_HEADERS {
        if let Some(v) = req.headers().get(*name) {
            headers.insert(HeaderName::from_static(name), v.clone());
        }
    }
    let body = match axum::body::to_bytes(req.into_body(), MAX_BODY).await {
        Ok(b) => b,
        Err(_) => {
            return api_error(
                StatusCode::PAYLOAD_TOO_LARGE,
                "too_large",
                "request body too large",
            )
        }
    };
    let sent = st
        .http
        .request(method, &url)
        .headers(headers)
        .bearer_auth(&token)
        .body(body)
        .send()
        .await;
    let resp = match sent {
        Ok(r) => r,
        Err(e) => {
            let why = if e.is_connect() || e.is_timeout() {
                "drone not reachable"
            } else {
                "request to drone failed"
            };
            tracing::debug!(drone = %id, error = %e, "proxy error");
            return api_error(StatusCode::BAD_GATEWAY, "drone_unreachable", why);
        }
    };
    let mut out = Response::builder().status(resp.status());
    for name in RESP_HEADERS {
        if let Some(v) = resp.headers().get(*name) {
            out = out.header(*name, v.clone());
        }
    }
    out.body(Body::from_stream(resp.bytes_stream()))
        .unwrap_or_else(|_| StatusCode::BAD_GATEWAY.into_response())
}

/// Browsers must open the socket from the hub's own page (no cross-site WebSocket hijacking).
pub fn same_origin(headers: &HeaderMap) -> bool {
    let Some(origin) = headers.get("origin").and_then(|v| v.to_str().ok()) else {
        return true;
    };
    let Some(host) = headers.get("host").and_then(|v| v.to_str().ok()) else {
        return false;
    };
    origin
        .strip_prefix("http://")
        .or_else(|| origin.strip_prefix("https://"))
        .is_some_and(|o| o == host)
}

pub async fn ws(
    State(st): State<Arc<AppState>>,
    Path(id): Path<String>,
    headers: HeaderMap,
    upgrade: WebSocketUpgrade,
) -> Response {
    if !same_origin(&headers) {
        return api_error(
            StatusCode::FORBIDDEN,
            "bad_origin",
            "cross-origin WebSocket refused",
        );
    }
    let (drone, token) = match find(&st, &id) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let url = format!("{}/ws/telemetry", ws_base(&drone.cfg.url));
    upgrade
        .max_message_size(64 * 1024)
        .on_upgrade(move |client| pump(client, url, token))
}

async fn pump(client: WebSocket, url: String, token: String) {
    let mut client = client;
    let req = url.into_client_request().ok().and_then(|mut r| {
        r.headers_mut().insert(
            "authorization",
            HeaderValue::from_str(&format!("Bearer {token}")).ok()?,
        );
        Some(r)
    });
    let upstream = match req {
        Some(r) => {
            tokio::time::timeout(Duration::from_secs(4), tokio_tungstenite::connect_async(r))
                .await
                .ok()
                .and_then(Result::ok)
        }
        None => None,
    };
    let Some((up, _)) = upstream else {
        let _ = client
            .send(AMsg::Close(Some(axum::extract::ws::CloseFrame {
                code: 1011,
                reason: "drone not reachable".into(),
            })))
            .await;
        return;
    };
    let (mut up_tx, mut up_rx) = up.split();
    let (mut cl_tx, mut cl_rx) = client.split();
    let down = async {
        while let Some(Ok(m)) = up_rx.next().await {
            let out = match m {
                TMsg::Text(t) => AMsg::Text(t.as_str().to_owned().into()),
                TMsg::Binary(b) => AMsg::Binary(b),
                TMsg::Close(_) => break,
                _ => continue,
            };
            if cl_tx.send(out).await.is_err() {
                break;
            }
        }
    };
    let upward = async {
        while let Some(Ok(m)) = cl_rx.next().await {
            let out = match m {
                AMsg::Text(t) => TMsg::Text(t.as_str().to_owned().into()),
                AMsg::Close(_) => break,
                _ => continue,
            };
            if up_tx.send(out).await.is_err() {
                break;
            }
        }
    };
    tokio::select! { _ = down => {}, _ = upward => {} }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths() {
        assert_eq!(
            upstream_path("/d/alpha/api/servo/nudge", "alpha").unwrap(),
            "/api/servo/nudge"
        );
        assert_eq!(
            upstream_path("/d/alpha/api/camera/snapshots/a%20b.jpg", "alpha").unwrap(),
            "/api/camera/snapshots/a%20b.jpg"
        );
        assert_eq!(
            upstream_path("/d/alpha/ws/telemetry", "alpha").unwrap(),
            "/ws/telemetry"
        );
        assert!(upstream_path("/d/alpha/api/../../etc", "alpha").is_err());
        assert!(upstream_path("/d/alpha/api/x/%2E%2E/y", "alpha").is_err());
        assert!(upstream_path("/d/alpha/api/x%2Fy", "alpha").is_err());
        assert_eq!(
            upstream_path("/d/alpha/api/auth/login", "alpha")
                .unwrap_err()
                .0,
            StatusCode::NOT_FOUND
        );
        assert!(upstream_path("/d/alpha/api/docs", "alpha").is_err());
        assert!(upstream_path("/d/alpha/other", "alpha").is_err());
    }

    #[test]
    fn origins() {
        let mut h = HeaderMap::new();
        assert!(same_origin(&h), "non-browser client");
        h.insert("host", HeaderValue::from_static("127.0.0.1:9000"));
        h.insert("origin", HeaderValue::from_static("http://127.0.0.1:9000"));
        assert!(same_origin(&h));
        h.insert("origin", HeaderValue::from_static("http://evil.example"));
        assert!(!same_origin(&h));
    }
}
