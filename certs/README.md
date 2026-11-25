# MOEX ISS trust anchor

`russian_trusted_root_ca.pem` was retrieved over verified HTTPS on 2026-11-25 from
https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt, the government certificate distribution server.
MOEX migration notice https://www.moex.com/n106155 and instructions https://www.moex.com/kak-sohranit-dostup-k-servisam-birzhi.

SHA-256 fingerprint `D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31`.

The additional root is loaded only for HTTPS requests to `iss.moex.com`.
System trust, hostname validation and certificate verification stay enabled.
Redirects to other hosts use normal system trust. Telegram and other sources use system trust.
When rotating the certificate, retrieve it from the official source, review its fingerprint and validity, and rerun the TLS tests.
