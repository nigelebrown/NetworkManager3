import httpx
B = "http://127.0.0.1:8000/api"
c = httpx.Client(timeout=30)
tok = c.post(B + "/auth/login", json={"username": "admin", "password": "correct-horse-battery"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
aps = [("34:3a:20:cc:b3:bc", "CNPCLJ51A1", "172.17.0.1", "NCB South Tower"),
       ("34:3a:20:cd:10:26", "CNPCLJ51A2", "10.77.1.2", "NCB South Tower"),
       ("34:8a:12:c8:c5:b2", "CNPCLJ51A3", "10.77.1.3", "NCB South Tower"),
       ("34:8a:12:c8:d0:2c", "CNPCLJ51B1", "172.18.0.1", "ICTD Building"),
       ("34:8a:12:c8:e5:14", "CNPCLJ51B2", "10.77.2.2", "ICTD Building"),
       ("34:8a:12:c8:e7:d8", "PHPBKPP9ZZ", "172.17.0.50", "MOUNT SALEM")]
devs = [{"family": "access_point", "site_name": s, "hostname": h, "serial_number": sn, "model": "AP-505",
         "firmware": "8.10.0.21_94501", "management_ip": ip} for h, sn, ip, s in aps]
devs += [{"family": "switch", "site_name": "ADMIRAL TOWN", "hostname": "AdmiralTown-SW-1", "serial_number": "TW35KNF1G5",
          "model": "6100 24G CL4 4SFP+ Swch (JL677A)", "management_ip": "172.17.0.60"},
         {"family": "switch", "site_name": "ALLMAN TOWN", "hostname": "AllmanTownPS-SW-1", "serial_number": "TW35KNF1G6",
          "model": "6100 24G CL4 4SFP+ Swch (JL677A)", "management_ip": "172.17.0.61"}]
r = c.post(B + "/v1/quick-devices/bulk", headers=H, json={"devices": devs})
print("import", r.status_code, r.text[:200])
# a second operator and an approver for the incident / optimization workflows
for u, role in (("noc.operator", "operator"), ("noc.approver", "approver")):
    r = c.post(B + "/v1/users", headers=H, json={"username": u, "role": role, "temporary_password": "Temp-pass-12345", "site_scope": "all"})
    print("user", u, r.status_code)
    h2 = {"Authorization": "Bearer " + c.post(B + "/auth/login", json={"username": u, "password": "Temp-pass-12345"}).json()["access_token"]}
    print(" pw", c.post(B + "/auth/change-password", headers=h2, json={"current_password": "Temp-pass-12345", "new_password": "Noc-pass-123456"}).status_code)
