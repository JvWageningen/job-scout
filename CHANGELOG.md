# Changelog
All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com)

<!-- version list -->

## v1.6.0 (2026-07-20)

### Features

- **search**: Optional Brave Search API fallback for reliable web search
  ([`6c9336d`](https://github.com/JvWageningen/job-scout/commit/6c9336d6ce5d2845f12c248bd70b3e828adc73d5))


## v1.5.1 (2026-07-20)

### Bug Fixes

- **prune+ui**: Auto-expire filled matches and surface new data in the dashboard
  ([`8d8547b`](https://github.com/JvWageningen/job-scout/commit/8d8547bc5db458234a11590404ed64c3c646b526))

- **web**: Send no-cache headers for static assets so UI updates aren't stale
  ([`4080b7c`](https://github.com/JvWageningen/job-scout/commit/4080b7ce5e6fc17e34a6a8cabb0e833ce43b152e))


## v1.5.0 (2026-07-20)

### Features

- **review**: Summarise how good each matched company is to work for
  ([`09dded3`](https://github.com/JvWageningen/job-scout/commit/09dded3a27556970ed85c4eb4ee39d8ce1c2a3c2))


## v1.4.0 (2026-07-20)

### Features

- **sources**: Link matched jobs to the employer's own posting and re-check availability
  ([`265b096`](https://github.com/JvWageningen/job-scout/commit/265b0962b04ca6be2fcc9c850da2b6fc17c07a25))


## v1.3.0 (2026-07-20)

### Bug Fixes

- **eval**: Catch below-min salaries, over-senior/management roles, and agency listings
  ([`4dbd6d1`](https://github.com/JvWageningen/job-scout/commit/4dbd6d1d2e67b53abb437aec82e6a491b0651b94))

### Features

- **prune**: Run auto-prune during pipeline runs when prune_enabled
  ([`59083d3`](https://github.com/JvWageningen/job-scout/commit/59083d3c6596f3e05aefb8ae33060c1511a7269c))


## v1.2.0 (2026-07-19)

### Features

- **prune**: Auto-detect and prune filled/closed vacancies
  ([`35cb8a7`](https://github.com/JvWageningen/job-scout/commit/35cb8a78d87902de67627c60b1ea80b3e41e0f34))


## v1.1.3 (2026-07-19)

### Bug Fixes

- **travel**: Estimate bike time from distance instead of OSRM's car-time bike profile
  ([`daa63da`](https://github.com/JvWageningen/job-scout/commit/daa63da33db80808debb5cfee8fb6dffa7667076))


## v1.1.2 (2026-07-19)

### Bug Fixes

- **pipeline**: Fail-open quick-eval on transient errors; dedupe cross-source notifications
  ([`f0f5b11`](https://github.com/JvWageningen/job-scout/commit/f0f5b115d85db57a2dcd76dd5be4f86f71247d2a))


## v1.1.1 (2026-07-09)

### Bug Fixes

- **web**: Stop infinite dashboard refresh loop on stale run status
  ([`92e8903`](https://github.com/JvWageningen/job-scout/commit/92e89039123ab50e0bdb999304a05c0ef580b1bf))


## v1.1.0 (2026-07-06)

### Bug Fixes

- Update cv_parser to handle structured CvRole objects
  ([`3aec400`](https://github.com/JvWageningen/job-scout/commit/3aec400a6905a5d21e9fd61a1254ff12e626434a))

- **config**: Allow editing global LLM settings after initial setup
  ([`515fae9`](https://github.com/JvWageningen/job-scout/commit/515fae9085c4c1bbbadf155b4ec778e9a3d959ac))

- **config**: Preserve list formatting when saving config via web API
  ([`de6ed92`](https://github.com/JvWageningen/job-scout/commit/de6ed92a8aae9e490beddc4644b52ed0c31ae2dc))

- **notify**: Wrap long smtp_port line to satisfy line-length lint
  ([`05b81f2`](https://github.com/JvWageningen/job-scout/commit/05b81f26ebe1e15102bcd55412472dd6ccb2ff22))

- **scheduler**: Add --all flag to cron command for multi-user support
  ([`6e96268`](https://github.com/JvWageningen/job-scout/commit/6e96268e2ed0740626e512f90ce9624cb72a0d32))

- **web**: Correct global-setup detection check
  ([`c047d87`](https://github.com/JvWageningen/job-scout/commit/c047d87002296ebc2e25a130ea3cb307e02ed5e8))

- **web**: Send user param when loading approval queue
  ([`f2d6578`](https://github.com/JvWageningen/job-scout/commit/f2d657859d61d1f3213285844f776b284715ab69))

### Chores

- Update lock file with transitive dependency updates
  ([`a886e9a`](https://github.com/JvWageningen/job-scout/commit/a886e9a67cbcc9fe2e28b9375314b5f61888bee4))

### Documentation

- **changelog**: Add PSR insertion flag, backfill v1.0.0 entry
  ([`aacf6de`](https://github.com/JvWageningen/job-scout/commit/aacf6de160e8f16e55e119801c8dbac47a2ff477))

- **phase-9**: Document llama-swap as supported local LLM backend
  ([`1eb5a22`](https://github.com/JvWageningen/job-scout/commit/1eb5a22563de0c18f5b3ba17bf029aab169c9c1a))

### Features

- Add configurable per-scrape keyword limits
  ([`6f6e1c4`](https://github.com/JvWageningen/job-scout/commit/6f6e1c4faea6e0ebfb096da6e52dc12ea9ab8d6a))

- Add per-user geocode and travel-time caching
  ([`fd83d77`](https://github.com/JvWageningen/job-scout/commit/fd83d77d2ad5a4b38dca2c89228a08e7918d098b))

- Add persisted run history and analytics view
  ([`a10dd06`](https://github.com/JvWageningen/job-scout/commit/a10dd061292601a398c32928f014626d8c1bc3a7))

- Bring web dashboard to parity with CLI-only capabilities
  ([`9207c7f`](https://github.com/JvWageningen/job-scout/commit/9207c7fc94340b6c538814b44970f68576d566a5))

- Persist compensation reasoning from LLM evaluation
  ([`728de20`](https://github.com/JvWageningen/job-scout/commit/728de2056c2442f1fd9a807c67070fbdfdaf2eac))

- **approval**: Implement application lifecycle & approval gating
  ([`c8bb757`](https://github.com/JvWageningen/job-scout/commit/c8bb7573b8dd8c19f619d3d6406a6eb9477fe6d4))

- **cover-letter**: Implement cover letter and screening question generation
  ([`0c549c4`](https://github.com/JvWageningen/job-scout/commit/0c549c4880260d06ea6a6a73a77ccd330bf0c2a4))

- **cv-parsing**: Implement LLM-based structured CV parsing with caching
  ([`0686b93`](https://github.com/JvWageningen/job-scout/commit/0686b934d9485a727601e741e37b87c2d9b68d05))

- **export**: Implement CSV and JSON export for job listings
  ([`7d49787`](https://github.com/JvWageningen/job-scout/commit/7d49787885ae7ba6634ca860425b02046147f307))

- **linkedin**: Add LinkedIn profile import for CV enrichment
  ([`bab9722`](https://github.com/JvWageningen/job-scout/commit/bab972267f8dab2b5f4aa8cf28767da2493c1711))

- **llm**: Auto-detect available models from local LLM servers
  ([`515fae9`](https://github.com/JvWageningen/job-scout/commit/515fae9085c4c1bbbadf155b4ec778e9a3d959ac))

- **notify**: Add opt-in daily digest notification mode
  ([`9398834`](https://github.com/JvWageningen/job-scout/commit/9398834fe3615752aca2c514678bdd0d15f46ad9))

- **notify**: Implement pluggable per-user notification channels
  ([`7df4a56`](https://github.com/JvWageningen/job-scout/commit/7df4a56edbccb1513089e6b144bb952361c61e59))

- **phase-5**: Implement company research and hiring-manager discovery
  ([`a270209`](https://github.com/JvWageningen/job-scout/commit/a2702093a282d317fa468c785f0f08355e0a36d5))

- **phase-6**: Implement interview preparation support
  ([`cdf5060`](https://github.com/JvWageningen/job-scout/commit/cdf5060c5f00ccd91d0e4c4ce06cff15db554035))

- **phase-8**: Implement MCP server integration for ChatGPT/Claude/Copilot plugins
  ([`30976d6`](https://github.com/JvWageningen/job-scout/commit/30976d6dd0aa40fd902c6ebb592fc219d0f77cf6))

- **resume**: Implement resume tailoring and PDF generation
  ([`6af9a3b`](https://github.com/JvWageningen/job-scout/commit/6af9a3bd2eb22227c3aec986315f997819affe78))

- **schedule**: Implement per-user scheduling with weekday selection and pause toggle
  ([`758b67f`](https://github.com/JvWageningen/job-scout/commit/758b67f80de388a5c34c5c61ef0a78fe2033c9a1))

- **scraper**: Add configurable jobspy source selection
  ([`6a528e2`](https://github.com/JvWageningen/job-scout/commit/6a528e2fa61ab968fbca0cbdf5d10defd635e176))

- **scraper**: Optional JavaScript rendering for custom sites
  ([`24ab67e`](https://github.com/JvWageningen/job-scout/commit/24ab67e884c57631cbaeb49af8e20601a2fbd89e))

- **ui**: Replace LLM model text inputs with select dropdowns
  ([`8b7f073`](https://github.com/JvWageningen/job-scout/commit/8b7f07361f6ce94fed9a7c6c163e3a62918404ba))

- **web**: Add optional shared-token authentication for dashboard
  ([`ad0cd34`](https://github.com/JvWageningen/job-scout/commit/ad0cd349922213ccf457770511e6ef0c4cd200bc))

- **web**: Add search/filter/sort for job lists on dashboard
  ([`e1ce771`](https://github.com/JvWageningen/job-scout/commit/e1ce7712df3e61aa590b219b652f66358f00accc))

### Testing

- **lifecycle**: Add comprehensive tests for job status lifecycle tracking
  ([`cfd53e9`](https://github.com/JvWageningen/job-scout/commit/cfd53e93725590b4f44388546d27ab59d1286781))


## [1.0.0] - 2026-07-06

### Added
- Initial Release

## [0.1.0] - 2026-04-02

### Added
- Initial project scaffold via VibeGen
