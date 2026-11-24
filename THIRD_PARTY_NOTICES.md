# Third-party notices

Watt Disco's own code is under the PolyForm Noncommercial License 1.0.0 (see `LICENSE`). It uses the open-source
software and fonts below, which keep their own licences. If you share Watt
Disco as a packaged download (an `.app`, `.exe` or similar), include this file,
`LICENSE` and the licence files in `src/wattdisco/web/fonts/` with it
(they are inside the package, so a wheel or PyInstaller build carries them).

## fit-tool (BSD-3-Clause)

Writes the .fit activity files. <https://github.com/shaonianche/python_fit_tool>

Its licence asks for this copyright notice and licence text in the documentation of
any binary distribution:

```text
Copyright 2021 Stages Cycling. All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above
      copyright notice, this list of conditions and the following
      disclaimer in the documentation and/or other materials provided
      with the distribution.
    * Neither the name of Stages Cycling nor the names of its
      contributors may be used to endorse or promote products derived
      from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Fonts

Bundled in `src/wattdisco/web/fonts/` as .woff2 files, each next to its licence.

| Font | Copyright | Licence | Licence file |
|---|---|---|---|
| Chakra Petch | Copyright 2018 The Chakra Petch Project Authors (https://github.com/m4rc1e/Chakra-Petch) | SIL Open Font License 1.1 | `web/fonts/OFL-ChakraPetch.txt` |
| Martian Mono | Copyright 2021 The Martian Mono Project Authors (https://github.com/evilmartians/mono) | SIL Open Font License 1.1 | `web/fonts/OFL-MartianMono.txt` |
| Yellowtail | Copyright (c) 2011 by Brian J. Bonislawsky DBA Astigmatic (AOETI) | Apache License 2.0 | `web/fonts/LICENSE-Yellowtail.txt` |

The fonts are unmodified, apart from Google Fonts' latin and latin-ext subsets.

## certifi (MPL-2.0)

The root certificates that `requests` and `stravalib` use for HTTPS. Watt Disco uses
certifi unmodified. Its source code is at <https://github.com/certifi/python-certifi>,
and the licence is at <https://mozilla.org/MPL/2.0/>.

## Strava

The upload screen shows Strava's official "Connect with Strava" button (`src/wattdisco/web/img/btn_strava_connect_with_orange*.png`),
unchanged, from Strava's brand guidelines (<https://developers.strava.com/guidelines/>). It belongs to Strava and is
not covered by Watt Disco's licence. Watt Disco contains no other Strava logos or images; the name is used in plain
text only. Watt Disco is not affiliated with or endorsed by Strava.

## Licence texts inside the app

The built app (`.app`, `.exe`) carries every licence text in its `licenses/` folder (on macOS:
`Watt Disco.app/Contents/Resources/licenses`; in the Windows `.exe` it is packed inside the file).
`packaging/licenses.py` collects them at build time:

- `licenses/python-packages/<name>-<version>/`: each bundled Python package's own licence files
  (LICENSE, COPYING, NOTICE, AUTHORS). The few packages that ship none (the pyobjc packages and
  proxy_tools, all MIT) get the text kept in `packaging/package-licenses/`.
- `licenses/python/LICENSE.txt`: the licence of the Python runtime inside the app (PSF License).
- `licenses/native/`: the C libraries inside that runtime (OpenSSL, mpdecimal, liblzma, zlib, Expat,
  libffi, bzip2, SQLite), from `packaging/native-licenses/`.

The build fails when a bundled package has no licence text, so a new dependency cannot slip in without one.

## Python runtime (PSF License 2.0)

The app includes a Python 3.12 runtime, © Python Software Foundation, under the PSF License
Agreement (<https://docs.python.org/3/license.html>). Its text is in `licenses/python/LICENSE.txt`.

## Python packages

Everything Watt Disco installs to run (`uv export --no-dev`), as reported by
`pip-licenses` and PyPI. "Used on" names the platform for packages that are only
installed there (the Bluetooth and window libraries differ per platform).

| Package | Version | Licence | Used on | Project |
|---|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | all | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | all | https://github.com/annotated-types/annotated-types |
| anyio | 4.16.0 | MIT | all | https://anyio.readthedocs.io/en/stable/versionhistory.html |
| arrow | 1.4.0 | Apache-2.0 | all | https://github.com/arrow-py/arrow |
| bleak | 3.0.2 | MIT | all | https://github.com/hbldh/bleak |
| bottle | 0.13.4 | MIT | all | http://bottlepy.org/ |
| certifi | 2026.10.23 | MPL-2.0 | all | https://github.com/certifi/python-certifi |
| cffi | 2.1.1 | MIT-0 | Windows | https://pypi.org/project/cffi/ |
| charset-normalizer | 3.5.2 | MIT | all | https://github.com/jawah/charset_normalizer/blob/master/CHANGELOG.md |
| click | 8.5.0 | BSD-3-Clause | all | https://github.com/pallets/click/ |
| clr-loader | 0.3.1 | MIT | Windows | https://github.com/pythonnet/clr-loader |
| dbus-fast | 5.2.0 | MIT | Linux | https://github.com/bluetooth-devices/dbus-fast |
| fastapi | 0.162.0 | MIT | all | https://github.com/fastapi/fastapi |
| fit-tool | 0.9.17 | BSD-3-Clause | all | https://github.com/shaonianche/python_fit_tool |
| flexcache | 0.3 | BSD | all | https://github.com/hgrecco/flexcache |
| flexparser | 0.4 | BSD | all | https://github.com/hgrecco/flexparser |
| h11 | 0.16.0 | MIT | all | https://github.com/python-hyper/h11 |
| httptools | 0.9.0 | MIT | all | https://github.com/MagicStack/httptools |
| idna | 3.20 | BSD-3-Clause | all | https://github.com/kjd/idna |
| opentelemetry-api | 1.45.2 | Apache-2.0 | all | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | all | https://github.com/pypa/packaging |
| pint | 0.26.2 | BSD-3-Clause | all | https://github.com/hgrecco/pint |
| platformdirs | 4.14.3 | MIT | all | https://github.com/tox-dev/platformdirs |
| proxy-tools | 0.1.0 | MIT | all | http://github.com/jtushman/proxy_tools |
| pycparser | 3.12 | BSD-3-Clause | Windows | https://github.com/eliben/pycparser |
| pydantic | 2.14.2 | MIT | all | https://github.com/pydantic/pydantic |
| pydantic-core | 2.50.2 | MIT | all | https://github.com/pydantic |
| pyobjc-core | 13.0 | MIT | macOS | https://github.com/ronaldoussoren/pyobjc |
| pyobjc-framework-cocoa | 13.0 | MIT | macOS | https://github.com/ronaldoussoren/pyobjc |
| pyobjc-framework-corebluetooth | 13.0 | MIT | macOS | https://github.com/ronaldoussoren/pyobjc |
| pyobjc-framework-libdispatch | 13.0 | MIT | macOS | https://github.com/ronaldoussoren/pyobjc |
| pyobjc-framework-quartz | 13.0 | MIT | macOS | https://github.com/ronaldoussoren/pyobjc |
| pyobjc-framework-security | 13.0 | MIT | macOS | https://github.com/ronaldoussoren/pyobjc |
| pyobjc-framework-uniformtypeidentifiers | 13.0 | MIT | macOS | https://github.com/ronaldoussoren/pyobjc |
| pyobjc-framework-webkit | 13.0 | MIT | macOS | https://github.com/ronaldoussoren/pyobjc |
| python-dateutil | 4.0.0 | BSD-3-Clause | all | https://github.com/dateutil/dateutil |
| python-dotenv | 1.2.4 | BSD-3-Clause | all | https://github.com/theskumar/python-dotenv |
| pythonnet | 3.2.1 | MIT | Windows | https://pythonnet.github.io/ |
| pywebview | 6.2.1 | BSD | all | https://pywebview.flowrl.com/ |
| pyyaml | 6.0.3 | MIT | all | https://pyyaml.org/ |
| qtpy | 2.4.3 | MIT | all | https://github.com/spyder-ide/qtpy |
| requests | 2.34.2 | Apache-2.0 | all | https://github.com/psf/requests |
| starlette | 1.10.0 | BSD-3-Clause | all | https://github.com/Kludex/starlette |
| stravalib | 2.6.0 | Apache-2.0 | all | https://github.com/stravalib/stravalib |
| typing-extensions | 4.16.0 | PSF-2.0 | all | https://github.com/python/typing_extensions |
| typing-inspection | 0.4.4 | MIT | all | https://github.com/pydantic/typing-inspection |
| tzdata | 2026.6 | Apache-2.0 | all | https://github.com/python/tzdata |
| urllib3 | 2.8.0 | MIT | all | https://github.com/urllib3/urllib3/blob/main/CHANGES.rst |
| uvicorn | 0.54.0 | BSD-3-Clause | all | https://uvicorn.dev/ |
| uvloop | 0.23.0 | Apache-2.0 or MIT | all | https://github.com/MagicStack/uvloop |
| watchfiles | 1.3.0 | MIT | all | https://github.com/samuelcolvin/watchfiles |
| websockets | 17.4 | BSD-3-Clause | all | https://github.com/python-websockets/websockets |
| winrt-runtime | 4.1.0 | MIT | Windows | https://github.com/pywinrt/pywinrt |
| winrt-windows-devices-bluetooth | 4!10.0.28000.2957 | MIT | Windows | https://github.com/pywinrt/pywinrt |
| winrt-windows-devices-bluetooth-advertisement | 4!10.0.28000.2957 | MIT | Windows | https://github.com/pywinrt/pywinrt |
| winrt-windows-devices-bluetooth-genericattributeprofile | 4!10.0.28000.2957 | MIT | Windows | https://github.com/pywinrt/pywinrt |
| winrt-windows-devices-enumeration | 4!10.0.28000.2957 | MIT | Windows | https://github.com/pywinrt/pywinrt |
| winrt-windows-devices-radios | 4!10.0.28000.2957 | MIT | Windows | https://github.com/pywinrt/pywinrt |
| winrt-windows-foundation | 4!10.0.28000.2957 | MIT | Windows | https://github.com/pywinrt/pywinrt |
| winrt-windows-foundation-collections | 4!10.0.28000.2957 | MIT | Windows | https://github.com/pywinrt/pywinrt |
| winrt-windows-storage-streams | 4!10.0.28000.2957 | MIT | Windows | https://github.com/pywinrt/pywinrt |
