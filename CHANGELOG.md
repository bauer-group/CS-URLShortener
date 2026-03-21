## [1.4.1](https://github.com/bauer-group/CS-URLShortener/compare/v1.4.0...v1.4.1) (2026-03-21)

### ⚡ Performance

* **config:** increased default web worker count to 8 ([da9d3e5](https://github.com/bauer-group/CS-URLShortener/commit/da9d3e5570c0ad22c069179c76ac2e7ed4268c35))

### ♻️ Refactoring

* **cli:** migrated to typer and rich for enhanced CLI experience ([c29e194](https://github.com/bauer-group/CS-URLShortener/commit/c29e194146890bf84c3e8a49a331d34bfdc6c2b9))

## [1.4.0](https://github.com/bauer-group/CS-URLShortener/compare/v1.3.0...v1.4.0) (2026-03-21)

### 🚀 Features

* **shlink-cli:** added visits and tags management commands ([8efb9aa](https://github.com/bauer-group/CS-URLShortener/commit/8efb9aab8354e19869f04fecf25a20c7b3a561d9))

## [1.3.0](https://github.com/bauer-group/CS-URLShortener/compare/v1.2.0...v1.3.0) (2026-03-21)

### 🚀 Features

* **shlink-backup:** expand backup scope with redirect rules and domains ([a5af0c7](https://github.com/bauer-group/CS-URLShortener/commit/a5af0c766a19f00f2ccf3f6f4c0e35f552bc95cf))

## [1.2.0](https://github.com/bauer-group/CS-URLShortener/compare/v1.1.2...v1.2.0) (2026-03-21)

### 🚀 Features

* **cli:** added API key management commands ([9e745b0](https://github.com/bauer-group/CS-URLShortener/commit/9e745b0e8904718a34ad183678be219b05011305))

## [1.1.2](https://github.com/bauer-group/CS-URLShortener/compare/v1.1.1...v1.1.2) (2026-03-21)

### 🐛 Bug Fixes

* **docker:** replaced wget with curl in healthchecks ([edfc7dd](https://github.com/bauer-group/CS-URLShortener/commit/edfc7dd385d4a1651286d69513b0b2beeb8fce12))

## [1.1.1](https://github.com/bauer-group/CS-URLShortener/compare/v1.1.0...v1.1.1) (2026-03-21)

### ⚡ Performance

* **postgres:** updated max_connections default from 16 to 32 ([9fce926](https://github.com/bauer-group/CS-URLShortener/commit/9fce9265a69478ffbac2628e7d84355ac942e598))

## [1.1.0](https://github.com/bauer-group/CS-URLShortener/compare/v1.0.0...v1.1.0) (2026-03-21)

### 🚀 Features

* **config:** added RoadRunner worker configuration and improved database connection management ([d34d407](https://github.com/bauer-group/CS-URLShortener/commit/d34d407124c1928129a06037f33022413cf9c662))

## [1.0.0](https://github.com/bauer-group/CS-URLShortener/compare/v0.3.0...v1.0.0) (2026-03-20)

### ⚠ BREAKING CHANGES

* **web-ui:** Admin UI is no longer accessible at /.ui path. Users must access via ui.go.bauer-group.com subdomain. Requires DNS configuration (CNAME or A record pointing to the same server).
```

### ♻️ Refactoring

* **web-ui:** migrate Admin UI to subdomain-based routing ([009e910](https://github.com/bauer-group/CS-URLShortener/commit/009e910d839683e55a2c983b762ed19a91b2d19d))

## [0.3.0](https://github.com/bauer-group/CS-URLShortener/compare/v0.2.0...v0.3.0) (2026-03-20)

### 🚀 Features

* **shlink-cli:** added CLI tool for Shlink short URL management ([d48bb19](https://github.com/bauer-group/CS-URLShortener/commit/d48bb190ecb9a50d605cfe2bd5769cab7c6c62b3))
* **tooling:** added CLI utilities and Traefik asset routing ([3ce3fc7](https://github.com/bauer-group/CS-URLShortener/commit/3ce3fc73167587923a3198547deccadc9373b1bf))

## [0.2.0](https://github.com/bauer-group/CS-URLShortener/compare/v0.1.0...v0.2.0) (2026-03-20)

### 🚀 Features

* **backup:** added visit data archival option ([54a0940](https://github.com/bauer-group/CS-URLShortener/commit/54a09400ecdce07385e8803e964ff3d8dfc3e201))

## [0.1.0](https://github.com/bauer-group/CS-URLShortener/compare/v0.0.0...v0.1.0) (2026-03-20)

### 🚀 Features

* **import:** added tagging support and improved error handling ([da61b40](https://github.com/bauer-group/CS-URLShortener/commit/da61b4067cfdecb3b76f8bf06b6209e08b8a79a2))
* Initial Release ([2aeaa29](https://github.com/bauer-group/CS-URLShortener/commit/2aeaa291ed8444796fc4bdc655d9f88410fe61ec))

### 🐛 Bug Fixes

* **shlink:** fixed urllib hanging on error responses ([fc1805b](https://github.com/bauer-group/CS-URLShortener/commit/fc1805b4b957a2846697f84def98fa0ad5bff0f1))
