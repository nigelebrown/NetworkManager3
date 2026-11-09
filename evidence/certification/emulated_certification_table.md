# Emulated certification runs (protocol emulator — NOT hardware evidence)

| Env | Model family | Firmware train | Operation | Result | Readback / rollback evidence |
|---|---|---|---|---|---|
| EMULATED | CX 6100 | 10.14 | read.backup_full_config | PASS | {"bytes": 29085} |
| EMULATED | CX 6100 | 10.14 | switch.vlan.define | PASS | {"op": {"op": "switch.vlan.define", "value": 4090}, "rendered": [{"method": "POST", "path": "/system/vlans", "json": {"id": 4090, "name": "V |
| EMULATED | CX 6100 | 10.14 | switch.port.access_vlan | PASS | {"op": {"op": "switch.port.access_vlan", "port": "1/1/20", "value": 4090}, "rendered": [{"method": "PATCH", "path": "/system/interfaces/1%2F |
| EMULATED | CX 6100 | 10.14 | switch.port.trunk_vlans | PASS | {"op": {"op": "switch.port.trunk_vlans", "port": "1/1/20", "value": [1, 4090]}, "rendered": [{"method": "PATCH", "path": "/system/interfaces |
| EMULATED | CX 6100 | 10.14 | switch.port.admin_state | PASS | {"op": {"op": "switch.port.admin_state", "port": "1/1/20", "value": "down"}, "rendered": [{"method": "PATCH", "path": "/system/interfaces/1% |
| EMULATED | CX 6100 | 10.14 | switch.port.poe | PASS | {"op": {"op": "switch.port.poe", "port": "1/1/20", "value": "disabled"}, "rendered": [{"method": "PATCH", "path": "/system/interfaces/1%2F1% |
| EMULATED | CX 6100 | 10.14 | switch.lldp | PASS | {"op": {"op": "switch.lldp", "port": "1/1/20", "value": "disabled"}, "rendered": [{"method": "PATCH", "path": "/system/interfaces/1%2F1%2F20 |
| EMULATED | CX 6100 | 10.14 | switch.lag.members | PASS | {"op": {"op": "switch.lag.members", "lag": "lag250", "value": ["1/1/21", "1/1/22"]}, "rendered": [{"method": "PUT", "path": "/system/interfa |
| EMULATED | CX 6100 | 10.14 | config.restore | PASS | {"matches_baseline": true} |
| EMULATED | CX 6100 | 10.14 | read.backup_full_config | PASS | {"bytes": 29085} |
| EMULATED | CX 6100 | 10.14 | switch.vlan.define | PASS | {"op": {"op": "switch.vlan.define", "value": 4090}, "rendered": [{"method": "POST", "path": "/system/vlans", "json": {"id": 4090, "name": "V |
| EMULATED | CX 6100 | 10.14 | switch.port.access_vlan | PASS | {"op": {"op": "switch.port.access_vlan", "port": "1/1/20", "value": 4090}, "rendered": [{"method": "PATCH", "path": "/system/interfaces/1%2F |
| EMULATED | CX 6100 | 10.14 | switch.port.trunk_vlans | PASS | {"op": {"op": "switch.port.trunk_vlans", "port": "1/1/20", "value": [1, 4090]}, "rendered": [{"method": "PATCH", "path": "/system/interfaces |
| EMULATED | CX 6100 | 10.14 | switch.port.admin_state | PASS | {"op": {"op": "switch.port.admin_state", "port": "1/1/20", "value": "down"}, "rendered": [{"method": "PATCH", "path": "/system/interfaces/1% |
| EMULATED | CX 6100 | 10.14 | switch.port.poe | PASS | {"op": {"op": "switch.port.poe", "port": "1/1/20", "value": "disabled"}, "rendered": [{"method": "PATCH", "path": "/system/interfaces/1%2F1% |
| EMULATED | CX 6100 | 10.14 | switch.lldp | PASS | {"op": {"op": "switch.lldp", "port": "1/1/20", "value": "disabled"}, "rendered": [{"method": "PATCH", "path": "/system/interfaces/1%2F1%2F20 |
| EMULATED | CX 6100 | 10.14 | switch.lag.members | PASS | {"op": {"op": "switch.lag.members", "lag": "lag250", "value": ["1/1/21", "1/1/22"]}, "rendered": [{"method": "PUT", "path": "/system/interfa |
| EMULATED | CX 6100 | 10.14 | config.restore | PASS | {"matches_baseline": true} |
| EMULATED | CX 6200 | 10.14 | read.backup_full_config | PASS | {"bytes": 29086} |
| EMULATED | CX 6200 | 10.14 | switch.vlan.define | PASS | {"op": {"op": "switch.vlan.define", "value": 4090}, "rendered": [{"method": "POST", "path": "/system/vlans", "json": {"id": 4090, "name": "V |
| EMULATED | CX 6200 | 10.14 | switch.port.access_vlan | PASS | {"op": {"op": "switch.port.access_vlan", "port": "1/1/20", "value": 4090}, "rendered": [{"method": "PATCH", "path": "/system/interfaces/1%2F |
| EMULATED | CX 6200 | 10.14 | switch.port.trunk_vlans | PASS | {"op": {"op": "switch.port.trunk_vlans", "port": "1/1/20", "value": [1, 4090]}, "rendered": [{"method": "PATCH", "path": "/system/interfaces |
| EMULATED | CX 6200 | 10.14 | switch.port.admin_state | PASS | {"op": {"op": "switch.port.admin_state", "port": "1/1/20", "value": "down"}, "rendered": [{"method": "PATCH", "path": "/system/interfaces/1% |
| EMULATED | CX 6200 | 10.14 | switch.port.poe | PASS | {"op": {"op": "switch.port.poe", "port": "1/1/20", "value": "disabled"}, "rendered": [{"method": "PATCH", "path": "/system/interfaces/1%2F1% |
| EMULATED | CX 6200 | 10.14 | switch.lldp | PASS | {"op": {"op": "switch.lldp", "port": "1/1/20", "value": "disabled"}, "rendered": [{"method": "PATCH", "path": "/system/interfaces/1%2F1%2F20 |
| EMULATED | CX 6200 | 10.14 | switch.lag.members | PASS | {"op": {"op": "switch.lag.members", "lag": "lag250", "value": ["1/1/21", "1/1/22"]}, "rendered": [{"method": "PUT", "path": "/system/interfa |
| EMULATED | CX 6200 | 10.14 | config.restore | PASS | {"matches_baseline": true} |
| EMULATED | AP-505 | 8.10 | read.backup_full_config | PASS | {"bytes": 328} |
| EMULATED | AP-505 | 8.10 | ap.client_match | PASS | {"op": {"op": "ap.client_match", "value": "disabled"}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": ["configure  |
| EMULATED | AP-505 | 8.10 | ap.band_steering | PASS | {"op": {"op": "ap.band_steering", "value": "balance-bands"}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": ["conf |
| EMULATED | AP-505 | 8.10 | ap.airtime_fairness | PASS | {"op": {"op": "ap.airtime_fairness", "value": "fair-access"}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": ["con |
| EMULATED | AP-505 | 8.10 | ap.radio.width | PASS | {"op": {"op": "ap.radio.width", "value": 20}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": ["configure terminal" |
| EMULATED | AP-505 | 8.10 | ap.radio.tx_power | PASS | {"op": {"op": "ap.radio.tx_power", "min_dbm": 10, "max_dbm": 17}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": [ |
| EMULATED | AP-505 | 8.10 | ap.radio.channel | PASS | {"op": {"op": "ap.radio.channel", "band": "5", "value": 44, "power": 14}, "rendered": [{"transport": "SSH-EXEC", "host": "emulated-vc", "lin |
| EMULATED | AP-505 | 8.10 | config.restore | UNSUPPORTED | {"error": "UNSUPPORTED: Full-config restore on Instant is a guided procedure (Replace device wizard)."} |
| EMULATED | AP-515 | 8.10 | read.backup_full_config | PASS | {"bytes": 328} |
| EMULATED | AP-515 | 8.10 | ap.client_match | PASS | {"op": {"op": "ap.client_match", "value": "disabled"}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": ["configure  |
| EMULATED | AP-515 | 8.10 | ap.band_steering | PASS | {"op": {"op": "ap.band_steering", "value": "balance-bands"}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": ["conf |
| EMULATED | AP-515 | 8.10 | ap.airtime_fairness | PASS | {"op": {"op": "ap.airtime_fairness", "value": "fair-access"}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": ["con |
| EMULATED | AP-515 | 8.10 | ap.radio.width | PASS | {"op": {"op": "ap.radio.width", "value": 20}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": ["configure terminal" |
| EMULATED | AP-515 | 8.10 | ap.radio.tx_power | PASS | {"op": {"op": "ap.radio.tx_power", "min_dbm": 10, "max_dbm": 17}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": [ |
| EMULATED | AP-515 | 8.10 | ap.radio.channel | PASS | {"op": {"op": "ap.radio.channel", "band": "5", "value": 44, "power": 14}, "rendered": [{"transport": "SSH-EXEC", "host": "emulated-vc", "lin |
| EMULATED | AP-515 | 8.10 | config.restore | UNSUPPORTED | {"error": "UNSUPPORTED: Full-config restore on Instant is a guided procedure (Replace device wizard)."} |
| EMULATED | AP-535 | 8.10 | read.backup_full_config | PASS | {"bytes": 328} |
| EMULATED | AP-535 | 8.10 | ap.client_match | PASS | {"op": {"op": "ap.client_match", "value": "disabled"}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": ["configure  |
| EMULATED | AP-535 | 8.10 | ap.band_steering | PASS | {"op": {"op": "ap.band_steering", "value": "balance-bands"}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": ["conf |
| EMULATED | AP-535 | 8.10 | ap.airtime_fairness | PASS | {"op": {"op": "ap.airtime_fairness", "value": "fair-access"}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": ["con |
| EMULATED | AP-535 | 8.10 | ap.radio.width | PASS | {"op": {"op": "ap.radio.width", "value": 20}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": ["configure terminal" |
| EMULATED | AP-535 | 8.10 | ap.radio.tx_power | PASS | {"op": {"op": "ap.radio.tx_power", "min_dbm": 10, "max_dbm": 17}, "rendered": [{"transport": "SSH-CONFIG", "host": "emulated-vc", "lines": [ |
| EMULATED | AP-535 | 8.10 | ap.radio.channel | PASS | {"op": {"op": "ap.radio.channel", "band": "5", "value": 44, "power": 14}, "rendered": [{"transport": "SSH-EXEC", "host": "emulated-vc", "lin |
| EMULATED | AP-535 | 8.10 | config.restore | UNSUPPORTED | {"error": "UNSUPPORTED: Full-config restore on Instant is a guided procedure (Replace device wizard)."} |
