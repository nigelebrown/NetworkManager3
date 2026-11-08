# IPv4 discovery-range planning — CIDR /9 support

Integrated in GitHub: accepts IPv4 CIDR definitions from **/9 to /32**, including
`128.0.0.0/9`. This range contains 8,388,608 IPv4 addresses, divided into
32,768 /24 preview batches. The UI displays at most 16 batches at once.

**This feature plans only. It never scans the network, connects to a host,
sends credentials, configures a device, or launches scheduled discovery.**

- Administrator interface: **Inventory → Discovery ranges**
- `POST /api/v1/discovery/ranges/plan`: validate/describe a CIDR
- `GET /api/v1/discovery/ranges/batches`: paginated /24 previews
- RFC1918 private ranges: `APPROVAL_REQUIRED`; planning is not approval.
- Non-RFC1918 scopes such as `128.0.0.0/9`:
  `DEFINITION_ONLY_PUBLIC_OR_RESERVED`; never eligible for automatic probing.
- Live read-only device probes continue to use separately approved **private**
  /24-/32 targets through `INM_READONLY_DEVICE_CIDRS`.
- Device configuration writes remain locked.

Validated with eight local Python unit tests for range bounds, pagination,
IP normalization, public/private distinctions and zero-traffic design.
Full application CI and CentOS deployment are separate gates.
