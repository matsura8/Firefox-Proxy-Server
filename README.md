# RF Stream Finder

Desktop spectrum scanner for HackRF that watches sweep output in real time, flags likely digital/data-bearing carriers, and overlays notable regions directly on the spectrum display.

## Features

- Launches `hackrf_sweep` and parses live CSV sweep rows from the official HackRF sweep format.
- Scores contiguous above-noise regions to identify likely digital streams, narrowband carriers, and other notable activity.
- Tags detections with likely signal bands and source categories based on center frequency.
- Draws a live spectrum view with highlighted candidate regions and a side panel listing center frequency, bandwidth, peak level, and confidence.
- Maintains a persistent "found signals" log so repeated hits stay visible for later investigation.
- Adds per-stream investigation that captures a short IQ clip around a selected signal, runs an IQ analyzer view, generates best-effort AM/FM audio output when appropriate, and stores the IQ/audio files on disk.
- Includes a simulation mode so the GUI can be tested without a HackRF attached.

## Requirements

- Python 3.11+
- HackRF host tools installed and available on `PATH` for hardware mode
- Tkinter support in Python

## Run

```powershell
python rf_stream_finder.py
```

Use `simulation` mode to preview the app on a machine without `hackrf_sweep`. Switch to `hardware` mode when the HackRF tools are installed and the radio is connected.
The default scan range opens to the full HackRF span (`1 MHz` through `6000 MHz`) with a coarse `5 MHz` bin width so the whole band is visible immediately.

## Testing

```powershell
python -m unittest -v
```

## Firefox Extension Proxy

`firefox_extension_proxy.py` launches Firefox under Selenium, installs one or more Firefox add-ons such as uBlock Origin, opens a target page inside that filtered browser session, and then exposes the live rendered session over local HTTP.

This is a browser-stream proxy, not a raw TCP/HTTP forwarding proxy. The filtering happens because Firefox itself performs the requests with its extensions active, and the local service republishes the filtered browser view through a local URL.

Requirements:

- Python 3.11+
- Firefox installed
- `geckodriver` installed and on `PATH` or passed with `--geckodriver`
- Python packages: `selenium` and `pillow`
- At least one add-on XPI if you want request filtering from Firefox extensions

Install the Python dependencies:

```powershell
python -m pip install selenium pillow
```

Run it with an add-on such as uBlock Origin:

```powershell
python firefox_extension_proxy.py `
  --start-url https://example.com `
  --addon C:\path\to\uBlock0_1.61.0.firefox.signed.xpi
```

Then open the local viewer:

```text
http://127.0.0.1:8787/
```

Useful endpoints:

- `/` shows a small control page and embedded live stream
- `/snapshot.jpg` returns a single current frame
- `/stream.mjpg` returns an MJPEG stream suitable for embedding in other local tools
- `/healthz` returns JSON status and the current upstream URL

Notes:

- Add-on filtering only applies to traffic Firefox itself generates. This tool does not turn Firefox into a transparent network proxy for other applications.
- `--profile-path` can be used if you already have a Firefox profile configured the way you want.
- Use `--no-headless` if you want to watch or interact with the Firefox window directly on the desktop.

## OPNsense LAN Host Sync

`opnsense_isc_static_reconcile.py` is intended to run directly on the OPNsense box. It scans a local IPv4 subnet, harvests live hosts from the ARP table, compares them against the ISC DHCPv4 static mappings for an OPNsense interface, and prepares any missing `staticmap` entries.

Dry-run example:

```sh
python3 opnsense_isc_static_reconcile.py \
  --subnet 192.168.1.0/24 \
  --interface lan \
  --write-config-copy /tmp/merged-config.xml
```

Apply changes back to OPNsense:

```sh
python3 opnsense_isc_static_reconcile.py \
  --subnet 192.168.1.0/24 \
  --interface lan \
  --apply
```

Notes:

- The script uses local FreeBSD/OPNsense `ping` and `arp -an`, so it should be run on the firewall itself.
- By default it reads and writes `/conf/config.xml` locally and makes a timestamped backup before applying changes.
- The default post-update command is `service isc-dhcpd restart`. If your OPNsense build needs a different apply step, pass `--apply-command`.
- OPNsense documents ISC DHCP as end-of-life and recommends migrating to Dnsmasq or Kea for newer deployments.

## Windows DNS Network Discovery

`windows_dns_network_discovery.py` scans a local IPv4 subnet from Windows, learns live IP and MAC pairs from the ARP table, tries to resolve hostnames by reverse DNS, NetBIOS, and `ping -a`, and then registers the discovered hosts in a Windows DNS forward lookup zone.

Preview a subnet and show the DNS plan without changing records:

```powershell
python windows_dns_network_discovery.py `
  --zone corp.example.com `
  --subnet 192.168.1.0/24 `
  --what-if
```

Auto-select the local interface subnet and write A plus PTR records:

```powershell
python windows_dns_network_discovery.py `
  --zone corp.example.com `
  --dns-server dc01.corp.example.com `
  --create-ptr `
  --update-existing
```

Notes:

- Run it in an elevated PowerShell session on a Windows machine that can reach the target subnet.
- The script uses `Get-NetIPAddress`, `arp`, `ping`, `nbtstat`, and the `DnsServer` PowerShell module.
- Without `--update-existing`, name collisions are reported as conflicts and are not overwritten.
- If `--subnet` is omitted, the script chooses the largest non-loopback IPv4 interface unless `--interface-alias` is provided.

## Notes

- The detector is heuristic. It identifies spectrum regions that look like structured data activity, but it does not demodulate or decode traffic.
- Stream investigation does best-effort automatic demodulation for analog-like signals and saves IQ clips for digital/unhandled signals. Protocol-specific digital decoding is intentionally left to external tools.
- HackRF sweep bin widths must stay within HackRF's supported range. The GUI validates the minimum width before starting a scan.
