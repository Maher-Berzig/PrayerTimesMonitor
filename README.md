# Prayer Times Monitor

An Islamic prayer times desktop application with system tray integration, desktop widget, and multi-language support.

## Features

- **Real-time prayer times calculation** using the PrayTimes algorithm
- **System tray integration** with countdown timer and visual reservoir icon
- **Desktop widget** with draggable, always-on-top display
- **Multi-language support** (English, French, Arabic)
- **Hijri calendar integration** with translated month names
- **Multiple calculation methods** (MWL, ISNA, Egypt, Makkah, Karachi)
- **Customizable cities** with manual entry or geocoding via OpenStreetMap
- **Prayer time notifications** with customizable duration
- **City management** (add, edit, delete with multi-language names)

## Screenshots

*(Add screenshots here)*

## Installation

### Prerequisites

- Python 3.7+
- PyQt5

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Run the Application

```bash
python PrayerTimesMonitor.py
```

> **Note:** The application expects an icon file named `PrayerTimesMonitor.png` in the same directory. You may need to create or provide this icon file.

## Usage

### System Tray
- Left-click the tray icon to show/hide the hover dialog with prayer times
- Right-click to access the context menu

### Context Menu
- **City**: Select from built-in cities or add your own
- **Method**: Choose your preferred calculation method
- **Settings**: Configure language, time format, and notifications
- **Desktop Widget**: Toggle the floating desktop widget
- **About**: View application information and credits

### Adding a City
1. Right-click the tray icon → City → Add City...
2. Enter the city name in at least one language (English, French, Arabic)
3. Click "Fetch Location" to auto-populate coordinates, or enter them manually
4. Adjust timezone and DST settings if needed
5. Click "Add"

### Desktop Widget
- Displays current time, date (Gregorian and Hijri), countdown to next prayer, and all prayer times
- Drag to reposition on your desktop
- Updates every second

## Configuration

Configuration is stored automatically:
- **Windows**: `%APPDATA%\PrayerTimesMonitor\config.json`
- **Linux/macOS**: `~/.config/PrayerTimesMonitor/config.json`

## Built-in Cities

- Mecca (🕋 protected, cannot be deleted)
- Cairo, Algiers, Amman, Baghdad, Bahrain
- Beirut, Damascus, Doha, Kuwait City
- Muscat, Riyadh, Sanaa, Tunis
- Abu Dhabi, Manama, Ramallah

## Calculation Methods

| Method | Fajr Angle | Isha Angle |
|--------|-----------|-----------|
| Muslim World League (MWL) | 18° | 17° |
| Islamic Society of North America (ISNA) | 15° | 15° |
| Egyptian General Authority of Survey | 19.5° | 17.5° |
| Umm Al-Qura University, Makkah | 18.5° | 90 min after Maghrib |
| University of Islamic Sciences, Karachi | 18° | 18° |

## Credits

- **Prayer Times Calculator** (ver 2.3) by [PrayTimes.org](http://praytimes.org)
- **hijri-converter** library for Hijri date conversions
- **PyQt5** for the graphical interface
- **OpenStreetMap Nominatim** for geocoding services

## Author

Maher Berzig

## License

This software is free to use and distribute.

## Requirements

See [requirements.txt](requirements.txt) for Python dependencies.

---

**Version:** 1.0.0
