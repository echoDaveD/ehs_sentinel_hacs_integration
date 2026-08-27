# [2.0.0](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/compare/v1.1.11...v2.0.0) (2026-08-27)


* feat!: add multi-device support with config migration ([#60](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/issues/60)) ([d6f1b86](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/d6f1b86265080e4f22603de0dff36a2662d71e5a))


### Bug Fixes

* add missing idle enum values for SERVICEOPERATION, OD_EEV_VALVE, INDOOR_DEFROST_STEP ([#59](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/issues/59)) ([6b37268](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/6b37268f7073560ffa8799b3a12ddf935186f85e))


### BREAKING CHANGES

* The integration now supports multiple EHS Sentinel
devices simultaneously. Existing single-device configurations are
automatically migrated to the new config schema on first load.
Device-specific service calls now require a device_id parameter.

* implement multi-device coordinator architecture with device_id-based
  resolution and service call routing
* pre-initialize coordinator data from nasa_repository at startup so
  all entities are registered immediately instead of being created
  dynamically on first data receipt
* add seen_once tracking to suppress unavailable state on first poll
  before the device has responded
* migrate config entries from legacy single-device schema to
  multi-device layout
* enhance TCP task lifecycle management and connection logging per
  device instance
* enhance legacy naming scheme fallback and config entry title-based
  log file naming
* add defrost operation options in NASA repository
* extend operation mode handling to include COOL in MessageProcessor
* update README with multi-device setup instructions and Waveshare
  wiring example

## [1.1.11](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/compare/v1.1.10...v1.1.11) (2026-07-28)


### Bug Fixes

* refactor value retrieval logic in MessageProcessor for improved clarity ([#57](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/issues/57)) ([30552d0](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/30552d0964d1968568b1d3c5efacb1a72a39b036))

## [1.1.10](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/compare/v1.1.9...v1.1.10) (2026-07-04)


### Bug Fixes

* add missing value definition for outdoor defrost operation steps ([#53](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/issues/53)) ([15e7293](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/15e72935b624a4d3043701719c8944a5de4a1475))

## [1.1.9](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/compare/v1.1.8...v1.1.9) (2026-06-28)


### Bug Fixes

* update temperature limits for NASA samsung_ehssentinel_indoorsettempwaterout and samsung_ehssentinel_intempwateroutlettargetzone2f ([#52](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/issues/52)) ([f90e66f](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/f90e66fd5ceff395b0e99b023189389911aa6b46))

## [1.1.8](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/compare/v1.1.7...v1.1.8) (2026-06-12)


### Bug Fixes

* enhance TCP connection handling with keepalive and timeout management  fixes [#51](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/issues/51) ([cf4d116](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/cf4d1165268f82e3071d107bf8fa74526b174ad6))

## [1.1.7](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/compare/v1.1.6...v1.1.7) (2026-05-12)


### Bug Fixes

* add option 2 (75°C) for ENUM_OUT_EHS_WATEROUT_TYPE for r290 heat pumps ([5b795d0](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/5b795d03f11e6f62c8c3a695cc017c7dd8234d51))

## [1.1.6](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/compare/v1.1.5...v1.1.6) (2026-04-12)


### Bug Fixes

* update DHW mode handling to use VALVE instead of POWER ([941f6dc](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/941f6dc61d3eb857cc89f8da2f41c0d3ecc12c8e))
* add new entities for Remote Controller Room Temp. Control (FSV 2093) and Booster Heater (FSV 3031/3033) in dashboard templates) ([941f6dc](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/941f6dc61d3eb857cc89f8da2f41c0d3ecc12c8e))

## [1.1.5](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/compare/v1.1.4...v1.1.5) (2026-03-12)


### Bug Fixes

* refactor symbol fix missing references ([728ad4a](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/728ad4af77754d936ea1721e3ffedea3a17ce138))

## [1.1.4](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/compare/v1.1.3...v1.1.4) (2026-03-12)


### Bug Fixes

* change read request confirmation logging in message_producer ([37c1145](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/37c114591f02b94105f69158fe9a5a40453d8329)), closes [#43](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/issues/43)
* rename commitlint.config file from js to cjs ([e8c0b57](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/e8c0b57cf6fa2541093c2c510f4a0bcdc8177da6))

## [1.1.3](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/compare/v1.1.2...v1.1.3) (2026-03-12)


### Bug Fixes

* conventional commit implementieren ([6069c5c](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/6069c5c3aad0b6d21b943fb42f5bcce1f5a2a6b8))
* fix naming in dashboards ( renames Power to Energy for Kwh entities) fix [#40](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/issues/40) ([58a29d1](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/commit/58a29d1e2be1b5103f269c4026adab47160b21fa))
