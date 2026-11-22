# Licences of the C libraries inside the Python runtime

The built app carries its own Python. Depending on the platform and the Python build, that runtime
contains some of these C libraries. The build copies all of these texts into the app's `licenses/native/`
folder, whether or not one platform's build uses all of them.

| Library | Used for | Licence |
|---|---|---|
| OpenSSL | HTTPS (Strava) | Apache License 2.0 (`OpenSSL-LICENSE.txt`) |
| mpdecimal | Python's `decimal` | BSD-2-Clause (`mpdecimal-COPYRIGHT.txt`) |
| liblzma (XZ Utils) | Python's `lzma` | 0BSD (`liblzma-0BSD.txt`) |
| zlib | compression | zlib licence |
| Expat | XML (workout import) | MIT |
| libffi | `ctypes` | MIT |
| bzip2 | Python's `bz2` | BSD-style |
| SQLite | Python's `sqlite3` | public domain |
