# Business metrics access contract

`/metrics/business/` is an internal monitoring endpoint, not a user-facing or
public API surface.

## Application boundary

Production requires `BUSINESS_METRICS_TOKEN`, a random secret of at least 32
characters. Prometheus (or the trusted scrape proxy) must send:

```text
Authorization: Bearer <BUSINESS_METRICS_TOKEN>
```

Missing or incorrect credentials return HTTP 404, and the application does not
render or read business metrics before authentication succeeds. Keep this token
in the monitoring secret store and rotate it independently from Django session,
JWT, database, and MinIO credentials.

## Network boundary

The edge proxy/firewall must restrict `/metrics/business/` to the monitoring
network or trusted scrape proxy. Do not publish this path through the external
API ingress. The bearer token is defense in depth, not a substitute for network
segmentation.

The Prometheus reference deployment does not invent an application hostname in
`prometheus.yml`; deployment automation must add the actual internal Django
service target and inject the bearer credential from its secret store.
