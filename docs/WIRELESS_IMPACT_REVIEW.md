# Wireless impact review and measurement contract

The Optimization page combines native Aruba ARM, persisted Digital Experience measurements and scoped Network Intelligence findings. It is read-only towards hardware. Its baseline target retains the application 20/40 MHz policy; it does not copy the saved reference's 80 MHz capability.

## Scope and provenance
Every preview lists controller, cluster/site names, physical locations when assigned, all registered member APs, current ARM evidence and proposed typed operations. The controller/member list must still be verified at execution. Unlinked, merged, simulated or out-of-scope records do not provide real evidence. Cluster-wide power bounds can affect both bands; the width operation permits wide channels only on 5 GHz.

Save a before review for up to five clusters within ten minutes before the intended approved execution. The service saves the actual historical measurement window and scoring-policy fingerprint. An after window requires an exact matching APPLIED_VERIFIED HARDWARE ChangeExecution after capture. Emulator, aborted, unrelated and unmatched changes cannot become successful after evidence. After ten minutes of settling, it observes the same configured window length. A bounded worker freezes completed evidence before ARM's short history expires.

A changing membership/firmware, other overlapping recorded hardware changes, a changed scoring policy, missing coverage, insufficient sample span or materially different client counts prevents an unqualified improvement claim. The evidence is observational; it does not establish causation. Changes made outside this application may not have complete execution evidence.

## Calculations
The existing configurable application link model is preserved: defaults are signal 45%, negotiated rate 20%, inferred stability 25% and retries 10%. Available weights are renormalized; original coverage and missing inputs remain visible. This is not a vendor's proprietary experience formula or an end-to-end service score. Invalid/nonfinite metrics cannot become perfect scores. A negotiated rate is explicitly labelled as PHY/link information.

Impact history uses only non-simulated SiteSample rows. It weights the site mean link score by the number of scored client observations (good+fair+poor). Those counts must be positive and no larger than connected clients. Poor percentage uses that same observation denominator. No observed clients means no score, not 100%. Means are sample/observation summaries, not independent unique-client success probabilities.

The comparison requires at least three distinct samples spanning 15 minutes, full site coverage, at least 80% client measurement coverage, repeated native ARM coverage on at least 80% of member APs, and client load within 20% (minimum tolerance two clients). Native ARM samples must include repeated observations spanning ten minutes. Application review thresholds flag a five-point link-score change or five percentage-point poor-client change; they are not claimed to be universal industry thresholds.

RF utilization, channel quality and radio retries are presented separately. Radio retries do not establish a client's retransmission rate. Poll-inferred disconnects/roams are distinct from device-reported ClientMatch actions and do not provide an attempt-success denominator.

## End-to-end gaps
Association/authentication and DHCP/DNS success/latency, client packet loss, application RTT/jitter and measured application throughput remain explicitly unmeasured unless their own telemetry exists. No proxy or AP health score fills those gaps. The impact service never fabricates a complete service score.

## Platform guidance
The dimensions and distinctions follow public platform documentation, not copied proprietary scores:
- Aruba connection stages and attempt denominators: https://arubanetworking.hpe.com/techdocs/central-on-premises/olh/304x/GUID-8EBA8118-96A9-4F85-BB35-0B5A8AF610FF.html
- Aruba ARM settings and Client Aware: https://arubanetworking.hpe.com/techdocs/CLI-Bank/Content/instant/arm.htm
- Mist SLE categories: https://www.mist.com/documentation/sle-api-documentation/
- Meraki health stages: https://documentation.meraki.com/Platform_Management/Dashboard_Administration/Operate_and_Maintain/Monitoring_and_Reporting/Meraki_Health_Overview

No hardware certification, approval or write gate is removed. This feature does not execute a rollout or automatically claim an optimized WLAN.
