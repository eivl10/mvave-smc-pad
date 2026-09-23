# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — [SemVer](https://semver.org/lang/ru/).

## [0.2.0] - 2026-09-23

### Added
- Settings export / import via the "Settings" menu; current settings are backed up before import / Экспорт и импорт настроек через меню «Настройки»; перед импортом текущие копируются рядом
- Controller battery level in the header / Заряд контроллера в шапке
- Shift and Note Repeat on the scheme, marked as firmware-only / Shift и Note Repeat на схеме, с пометкой «обрабатывает прошивка»
- One-file `SMC-PAD.exe` build, no Python required / Сборка одного `SMC-PAD.exe`, Python не нужен

### Changed
- Modern header and side panel on CustomTkinter: cards, switches, pad tabs "Action / Color" / Современные шапка и панель справа на CustomTkinter: карточки, переключатели, вкладки пэда «Действие / Цвет»
- Button labels are truncated with "…" instead of being cut off by the panel edge / Подписи кнопок обрезаются с «…», а не краем панели
- In the exe, settings live next to the exe / В exe настройки хранятся рядом с ним

## [0.1.0] - 2026-09-23

### Added
- First public release: BLE bridge, 82 actions, tray, volume indicator, single instance / Первый публичный выпуск: BLE-мост, 82 действия, трей, индикатор громкости, один экземпляр
