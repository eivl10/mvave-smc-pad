# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — [SemVer](https://semver.org/lang/ru/).

## [0.3.0] - 2026-09-23

### Added
- Presets inside the app: "Settings → Presets" lists saved and previously imported settings, open or delete without a file dialog / Пресеты внутри программы: «Настройки → Пресеты» — сохранённые и загруженные раньше настройки, открыть или удалить без выбора файла
- Light theme and a theme switch (Windows / dark / light) / Светлая тема и переключатель темы (как в Windows / тёмная / светлая)
- Own colour picker; picked colours stay in the palette, right-click removes / Свой выбор цвета; выбранные цвета остаются в палитре, правый клик убирает

### Changed
- Own notifications and dialogs instead of system message boxes / Свои уведомления и окна вместо системных
- Borderless settings menu / Меню настроек без обводки
- BT, PAD BANK and KNOB BANK are grey like Shift and Note Repeat: the controller handles them itself / BT, PAD BANK и KNOB BANK серые, как Shift и Note Repeat: их обрабатывает сам контроллер
- Removed service captions ("colour goes to the device" and similar) / Убраны служебные надписи («цвет идёт на устройство» и подобные)
- Backups before import go to `presets/_backups` / Резервные копии перед загрузкой лежат в `presets/_backups`

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
