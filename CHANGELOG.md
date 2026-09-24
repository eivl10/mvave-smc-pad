# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — [SemVer](https://semver.org/lang/ru/).

## [1.0.0] - 2026-09-24

Config and preset format is considered stable from now on: incompatible changes only with a migration / Формат конфига и пресетов с этой версии стабилен: несовместимые изменения — только с миграцией

### Added
- "Start with Windows" switch in Settings: the app starts hidden in the tray / Переключатель «Запускать вместе с Windows» в настройках: программа стартует спрятанной в трей
- Desktop shortcut from Settings, works in the exe / Ярлык на рабочем столе из настроек, работает и в exe
- Lock between knob sides: picking an action on one side sets the opposite on the other / Замок между сторонами крутилки: выбор действия на одной стороне ставит на другую противоположное
- Help window (the "i" button) / Окно справки (кнопка «i»)
- Window size and position are remembered / Размер и место окна запоминаются

### Changed
- Smooth rounded corners and smooth knobs / Гладкие скругления углов и гладкие крутилки
- Element names in Latin: PAD 6, KNOB 6, BUTTON; back / forward buttons have their own icons / Имена элементов латиницей: PAD 6, KNOB 6, BUTTON; у кнопок «назад / вперёд» свои значки
- "Identify" is now "Pick by press"; bank indicator is a neutral "Pad bank · Knob bank" caption / «Определить» теперь «Выбор нажатием»; индикатор банка — нейтральная подпись «Pad bank · Knob bank»

### Fixed
- The bottom of the side panel was cut off in the "Left / right" knob mode / Низ панели справа срезался в режиме крутилки «Влево / вправо»
- A knob at the controller's end stop keeps moving the value if the controller repeats 0 / 127 (not yet confirmed on hardware, see docs/NEXT.md) / Крутилка на упоре контроллера продолжает менять значение, если он повторяет 0 / 127 (на железе не подтверждено, см. docs/NEXT.md)

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
