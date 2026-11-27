# Proviso API Reference

Local API base URL: `http://127.0.0.1:8000`.

## Authentication

### `POST /auth/register`

Creates a durable user in local SQLite and returns access and refresh JWTs.

```json
{"username":"analyst","password":"a-secure-password","full_name":"Analyst Name"}
```

### `POST /auth/login`

Returns access and refresh JWTs for an existing user.

```json
{"username":"admin","password":"admin123"}
```

### `POST /auth/refresh`

Exchanges a refresh token for a new access/refresh pair.

```json
{"refresh_token":"<refresh-token>"}
```

Use the access token on protected endpoints:

```text
Authorization: Bearer <access-token>
```

## Analysis

### `POST /query`

Protected endpoint for a contract, finance, or cross-domain request.

```json
{"query":"Are we overpaying Mcclain Miller and Henderson based on their contract terms?"}
```

The response contains:

- `answer`: evidence-bounded analysis or a human-review handoff.
- `cited_sources`: document filename, page, vendor, clause, and relevance.
- `sql_queries`: read-only statements actually executed.
- `agent_trace`: supervisor/legal/finance transitions.
- `review_escalations`: structured reason, recommended action, and optional
  retrieval confidence when Proviso declines a conclusion.

The route limits queries to 2,000 characters and applies per-client rate
limiting. SQL-like destructive input is rejected before agent execution.

## Feedback

### `POST /feedback`

Protected endpoint that stores a 1–5 rating in SQLite.

```json
{
  "query_text":"Did the monthly total breach the cap?",
  "response_text":"The cited contract cap was exceeded.",
  "rating":5,
  "comment":"Clear trace."
}
```

### `GET /feedback?limit=100`

Returns the caller's feedback records. An administrator can view all records.
The maximum limit is 500.

## System endpoints

- `GET /health`: service, version, and status.  It also returns a credential-free
  `operating_mode` object: whether hosted or local open-ended reasoning is
  configured and whether the local deterministic cap-audit inputs are present.
  An Ollama configuration is not reported as service-verified, because this
  lightweight health endpoint does not make a network call to the model server.
- `GET /docs`: interactive OpenAPI documentation.
- `GET /redoc`: ReDoc API view.

## Error behavior

- `401`: missing, expired, or invalid JWT.
- `409`: duplicate username during registration.
- `422`: invalid request payload.
- `429`: rate limit exceeded.
- `500`: unavailable model/vector/database dependency or another internal
  error. Do not treat a `500` as a compliance conclusion.
