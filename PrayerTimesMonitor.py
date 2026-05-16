import sys
import json
import os
import math
import re
from PyQt5.QtWidgets import (QApplication, QSystemTrayIcon, QMenu, QAction, 
                             QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
                             QWidget, QLineEdit, QSpinBox, QComboBox, QCheckBox,
                             QFormLayout, QMessageBox, QFrame)
from PyQt5.QtGui import QIcon, QPixmap, QColor, QPainter, QPen, QFont
from PyQt5.QtCore import QTimer, Qt, QPoint, QTime
from datetime import datetime, timedelta, date
from hijri_converter import Gregorian, Hijri
import warnings
import requests
warnings.filterwarnings("ignore", category=DeprecationWarning)

# ── Windows startup registry helpers ─────────────────────────────────────────
_STARTUP_REG_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_APP_NAME = "PrayerTimesMonitor"


def _get_app_exe_path():
    """Return the command that should be written to the registry."""
    if getattr(sys, 'frozen', False):
        # PyInstaller bundle: point directly at the .exe
        return sys.executable
    else:
        # Plain .py script: call the current Python interpreter
        return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def get_startup_enabled():
    """Return True if the app is registered to run at Windows startup."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_REG_KEY, 0,
                             winreg.KEY_READ)
        winreg.QueryValueEx(key, _STARTUP_APP_NAME)
        winreg.CloseKey(key)
        return True
    except (FileNotFoundError, OSError):
        return False


def set_startup_enabled(enable: bool):
    """Add or remove the app from the Windows startup registry key."""
    if sys.platform != "win32":
        return
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_REG_KEY, 0,
                             winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, _STARTUP_APP_NAME, 0,
                              winreg.REG_SZ, _get_app_exe_path())
        else:
            try:
                winreg.DeleteValue(key, _STARTUP_APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception:
        pass
# ─────────────────────────────────────────────────────────────────────────────


# -------------------------------
# PrayTimes Class (from TimeToPray.py)
# -------------------------------
class PrayTimes():
    timeNames = {
        'imsak'    : 'Imsak',
        'fajr'     : 'Fajr',
        'sunrise'  : 'Sunrise',
        'dhuhr'    : 'Dhuhr',
        'asr'      : 'Asr',
        'sunset'   : 'Sunset',
        'maghrib'  : 'Maghrib',
        'isha'     : 'Isha',
        'midnight' : 'Midnight'
    }

    methods = {
        'MWL': {
            'name': 'Muslim World League',
            'params': { 'fajr': 18, 'isha': 17 } },
        'ISNA': {
            'name': 'Islamic Society of North America (ISNA)',
            'params': { 'fajr': 15, 'isha': 15 } },
        'Egypt': {
            'name': 'Egyptian General Authority of Survey',
            'params': { 'fajr': 19.5, 'isha': 17.5 } },
        'Makkah': {
            'name': 'Umm Al-Qura University, Makkah',
            'params': { 'fajr': 18.5, 'isha': '90 min' } },
        'Karachi': {
            'name': 'University of Islamic Sciences, Karachi',
            'params': { 'fajr': 18, 'isha': 18 } },
    }

    defaultParams = {
        'maghrib': '0 min', 'midnight': 'Standard'
    }

    calcMethod = 'MWL'
    
    settings = {
        "imsak"    : '10 min',
        "dhuhr"    : '0 min',
        "asr"      : 'Standard',
        "highLats" : 'NightMiddle'
    }
    
    timeFormat = '24h'
    timeSuffixes = ['am', 'pm']
    invalidTime =  '-----'
    numIterations = 1
    offset = {}

    def __init__(self, method = "MWL"):
        for method, config in self.methods.items():
            for name, value in self.defaultParams.items():
                if not name in config['params'] or config['params'][name] is None:
                    config['params'][name] = value

        self.calcMethod = method if method in self.methods else 'MWL'
        params = self.methods[self.calcMethod]['params']
        for name, value in params.items():
            self.settings[name] = value

        for name in self.timeNames:
            self.offset[name] = 0

    def setMethod(self, method):
        if method in self.methods:
            self.calcMethod = method
            # Update settings with the new method's parameters
            params = self.methods[method]['params']
            for name, value in params.items():
                self.settings[name] = value 
            

    def adjust(self, params):
        self.settings.update(params)

    def tune(self, timeOffsets):
        self.offset.update(timeOffsets)
            
    def getMethod(self):
        return self.calcMethod

    def getSettings(self):
        return self.settings
        
    def getOffsets(self):
        return self.offset

    def getDefaults(self):
        return self.methods

    def getTimes(self, date, coords, timezone, dst = 0, format = None):
        self.lat = coords[0]
        self.lng = coords[1]
        self.elv = coords[2] if len(coords)>2 else 0
        if format != None:
            self.timeFormat = format
        if type(date).__name__ == 'date':
            date = (date.year, date.month, date.day)
        self.timeZone = timezone + (1 if dst else 0)
        self.jDate = self.julian(date[0], date[1], date[2]) - self.lng / (15 * 24.0)
        return self.computeTimes()
    
    def getFormattedTime(self, time, format, suffixes = None):
        if math.isnan(time):
            return self.invalidTime
        if format == 'Float':
            return time
        if suffixes == None:
            suffixes = self.timeSuffixes

        time = self.fixhour(time+ 0.5/ 60)
        hours = math.floor(time)
        
        minutes = math.floor((time- hours)* 60)
        suffix = suffixes[ 0 if hours < 12 else 1 ] if format == '12h' else ''
        formattedTime = "%02d:%02d" % (hours, minutes) if format == "24h" else "%d:%02d" % ((hours+11)%12+1, minutes)
        return formattedTime + suffix

    def midDay(self, time):
        eqt = self.sunPosition(self.jDate + time)[1]
        return self.fixhour(12 - eqt)

    def sunAngleTime(self, angle, time, direction = None):
        try:
            decl = self.sunPosition(self.jDate + time)[0]
            noon = self.midDay(time)
            t = 1/15.0* self.arccos((-self.sin(angle)- self.sin(decl)* self.sin(self.lat))/
                    (self.cos(decl)* self.cos(self.lat)))
            return noon+ (-t if direction == 'ccw' else t)
        except ValueError:
            return float('nan')

    def asrTime(self, factor, time): 
        decl = self.sunPosition(self.jDate + time)[0]
        angle = -self.arccot(factor + self.tan(abs(self.lat - decl)))
        return self.sunAngleTime(angle, time)

    def sunPosition(self, jd):
        D = jd - 2451545.0
        g = self.fixangle(357.529 + 0.98560028* D)
        q = self.fixangle(280.459 + 0.98564736* D)
        L = self.fixangle(q + 1.915* self.sin(g) + 0.020* self.sin(2*g))

        R = 1.00014 - 0.01671*self.cos(g) - 0.00014*self.cos(2*g)
        e = 23.439 - 0.00000036* D

        RA = self.arctan2(self.cos(e)* self.sin(L), self.cos(L))/ 15.0
        eqt = q/15.0 - self.fixhour(RA)
        decl = self.arcsin(self.sin(e)* self.sin(L))

        return (decl, eqt)
        
    def julian(self, year, month, day):
        if month <= 2:
            year -= 1
            month += 12
        A = math.floor(year / 100)
        B = 2 - A + math.floor(A / 4)
        return math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + B - 1524.5

    def computePrayerTimes(self, times):
        times = self.dayPortion(times)
        params = self.settings
        
        imsak   = self.sunAngleTime(self.eval(params['imsak']), times['imsak'], 'ccw')
        fajr    = self.sunAngleTime(self.eval(params['fajr']), times['fajr'], 'ccw')
        sunrise = self.sunAngleTime(self.riseSetAngle(self.elv), times['sunrise'], 'ccw')
        dhuhr   = self.midDay(times['dhuhr'])
        asr     = self.asrTime(self.asrFactor(params['asr']), times['asr'])
        sunset  = self.sunAngleTime(self.riseSetAngle(self.elv), times['sunset'])
        maghrib = self.sunAngleTime(self.eval(params['maghrib']), times['maghrib'])
        isha    = self.sunAngleTime(self.eval(params['isha']), times['isha']) 
        return {
            'imsak': imsak, 'fajr': fajr, 'sunrise': sunrise, 'dhuhr': dhuhr,
            'asr': asr,  'maghrib': maghrib, 'sunset': sunset, 'isha': isha
        }

    def computeTimes(self):
        times = {
            'imsak': 5, 'fajr': 5, 'sunrise': 6, 'dhuhr': 12,
            'asr': 13,  'maghrib': 18, 'sunset': 18, 'isha': 18
        }
        for i in range(self.numIterations):
            times = self.computePrayerTimes(times)
        times = self.adjustTimes(times)
        if self.settings['midnight'] == 'Jafari':
            times['midnight'] = times['sunset'] + self.timeDiff(times['sunset'], times['fajr']) / 2
        else:
            times['midnight'] = times['sunset'] + self.timeDiff(times['sunset'], times['sunrise']) / 2

        times = self.tuneTimes(times)
        return self.modifyFormats(times)
        
    def adjustTimes(self, times):
        params = self.settings
        tzAdjust = self.timeZone - self.lng / 15.0
        for t,v in times.items():
            times[t] += tzAdjust

        if params['highLats'] != 'None':
            times = self.adjustHighLats(times)

        if self.isMin(params['imsak']):
            times['imsak'] = times['fajr'] - self.eval(params['imsak']) / 60.0
        if self.isMin(params['maghrib']):
            times['maghrib'] = times['sunset'] - self.eval(params['maghrib']) / 60.0

        if self.isMin(params['isha']):
            times['isha'] = times['maghrib'] - self.eval(params['isha']) / 60.0
        times['dhuhr'] += self.eval(params['dhuhr']) / 60.0

        return times

    def asrFactor(self, asrParam):
        methods = {'Standard': 1, 'Hanafi': 2}
        return methods[asrParam] if asrParam in methods else self.eval(asrParam)

    def riseSetAngle(self, elevation = 0):
        elevation = 0 if elevation == None else elevation
        return 0.833 + 0.0347 * math.sqrt(elevation)

    def tuneTimes(self, times):
        for name, value in times.items():
            times[name] += self.offset[name] / 60.0
        return times

    def modifyFormats(self, times):
        for name, value in times.items():
            times[name] = self.getFormattedTime(times[name], self.timeFormat)
        return times
    
    def adjustHighLats(self, times):
        params = self.settings
        nightTime = self.timeDiff(times['sunset'], times['sunrise'])
        times['imsak'] = self.adjustHLTime(times['imsak'], times['sunrise'], self.eval(params['imsak']), nightTime, 'ccw')
        times['fajr']  = self.adjustHLTime(times['fajr'], times['sunrise'], self.eval(params['fajr']), nightTime, 'ccw')
        times['isha']  = self.adjustHLTime(times['isha'], times['sunset'], self.eval(params['isha']), nightTime)
        times['maghrib'] = self.adjustHLTime(times['maghrib'], times['sunset'], self.eval(params['maghrib']), nightTime)
        return times

    def adjustHLTime(self, time, base, angle, night, direction = None):
        portion = self.nightPortion(angle, night)
        diff = self.timeDiff(time, base) if direction == 'ccw' else self.timeDiff(base, time)
        if math.isnan(time) or diff > portion:
            time = base + (-portion if direction == 'ccw' else portion)
        return time

    def nightPortion(self, angle, night):
        method = self.settings['highLats']
        portion = 1/2.0
        if method == 'AngleBased':
            portion = 1/60.0 * angle
        if method == 'OneSeventh':
            portion = 1/7.0
        return portion * night

    def dayPortion(self, times):
        for i in times:
            times[i] /= 24.0
        return times

    def timeDiff(self, time1, time2):
        return self.fixhour(time2- time1)

    def eval(self, st):
        val = re.split('[^0-9.+-]', str(st), 1)[0]
        return float(val) if val else 0

    def isMin(self, arg):
        return isinstance(arg, str) and arg.find('min') > -1

    def sin(self, d): return math.sin(math.radians(d))
    def cos(self, d): return math.cos(math.radians(d))
    def tan(self, d): return math.tan(math.radians(d))

    def arcsin(self, x): return math.degrees(math.asin(x))
    def arccos(self, x): return math.degrees(math.acos(x))
    def arctan(self, x): return math.degrees(math.atan(x))

    def arccot(self, x): return math.degrees(math.atan(1.0/x))
    def arctan2(self, y, x): return math.degrees(math.atan2(y, x))

    def fixangle(self, angle): return self.fix(angle, 360.0)
    def fixhour(self, hour): return self.fix(hour, 24.0)

    def fix(self, a, mode):
        if math.isnan(a):
            return a
        a = a - mode * (math.floor(a / mode))
        return a + mode if a < 0 else a

# -------------------------------
# Language System
# -------------------------------
TRANSLATIONS = {
    'en': {
        'author_name':'Maher Berzig',
        'app_name': 'Prayer Times Monitor',
        'city': 'City',
        'method': 'Method',
        'add_city': 'Add City...',
        'settings': 'Settings',
        'desktop_widget': 'Desktop Widget',
        'quit': 'Quit',
        'next_prayer': 'Next Prayer',
        'time_remaining': 'Time remaining',
        'close': 'Close',
        'timezone': 'Timezone',
        'dst_active': 'Daylight Saving Time',
        'time_format': 'Time Format',
        '24h_format': '24-hour',
        '12h_format': '12 hours (AM/PM)',
        'prayers': {
            'fajr': 'Fajr',
            'sunrise': 'Sunrise',
            'dhuhr': 'Dhuhr',
            'asr': 'Asr',
            'maghrib': 'Maghrib',
            'sunset': 'Sunset',
            'isha': 'Isha'
        },
        'cities': {
            'Mecca': 'Mecca',
            'Cairo': 'Cairo',
            'Algiers': 'Algiers',
            'Amman': 'Amman',
            'Baghdad': 'Baghdad',
            'Bahrain': 'Bahrain',
            'Beirut': 'Beirut',
            'Damascus': 'Damascus',
            'Doha': 'Doha',
            'Kuwait City': 'Kuwait City',
            'Muscat': 'Muscat',
            'Riyadh': 'Riyadh',
            'Sanaa': 'Sanaa',
            'Tunis': 'Tunis',
            'Abu Dhabi': 'Abu Dhabi',
            'Manama': 'Manama',
            'Ramallah': 'Ramallah'
        },
        'cannot_delete_mecca': 'Cannot delete Mecca. This is a protected city.',
        'methods': {
            'MWL': 'Muslim World League',
            'ISNA': 'Islamic Society of North America',
            'Egypt': 'Egyptian General Authority of Survey',
            'Makkah': 'Umm al-Qura University, Makkah',
            'Karachi': 'University of Islamic Sciences, Karachi'
        },
        'city_name': 'City Name',
        'latitude': 'Latitude',
        'longitude': 'Longitude',
        'add': 'Add',
        'cancel': 'Cancel',
        'notification_duration': 'Notification Duration (seconds)',
        'show_notification': 'Show notification at prayer time',
        'language': 'Language',
        'save': 'Save',
        'add_new_city': 'Add New City',
        'error': 'Error',
        'invalid_coords': 'Invalid coordinates. Please enter valid numbers.',
        'city_exists': 'This city already exists.',
        'fetching_location': 'Fetch Location',
        'location_source': 'Source',
        'manual_entry': 'Manual Entry',
        'edit_city': 'Edit City...',
        'edit_selected_city': 'Edit City',
        'delete_city': 'Delete City',
        'city_name_en': 'City Name (English)',
        'city_name_fr': 'City Name (French)',
        'city_name_ar': 'City Name (Arabic)',
        'select_city_to_edit': 'Please select a city from the City menu first.',
        'update': 'Update',
        'restore_defaults': 'Restore Default Configuration',
        'confirm_restore': 'Confirm Restore',
        'confirm_restore_msg': 'Are you sure you want to restore the default configuration? This will reset all cities to their original values.',
        'confirm_delete': 'Confirm Delete',
        'confirm_delete_msg': 'Are you sure you want to delete this city?',
        'cannot_delete_current': 'Cannot delete the currently selected city. Please select another city first.',
        'months': {
            1: 'January', 2: 'February', 3: 'March', 4: 'April',
            5: 'May', 6: 'June', 7: 'July', 8: 'August',
            9: 'September', 10: 'October', 11: 'November', 12: 'December'
        },
        'hijri_months': {
            1: 'Muharram', 2: 'Safar', 3: 'Rabi\' al-Awwal', 4: 'Rabi\' al-Thani',
            5: 'Jumada al-Awwal', 6: 'Jumada al-Thani', 7: 'Rajab', 8: 'Sha\'ban',
            9: 'Ramadan', 10: 'Shawwal', 11: 'Dhu al-Qi\'dah', 12: 'Dhu al-Hijjah'
        },
        'success': 'Success',
        'location_found': 'Location found successfully!',
        'city_not_found': 'City not found',
        'fetched_from': 'Fetched from',
        'invalid_response': 'Invalid response from geocoding service',
        'about': 'About',
        'about_title': 'About Prayer Times Monitor',
        'version': 'Version',
        'author': 'Author',
        'description': 'Description',
        'prayer_calc': 'Prayer Times Calculator',
        'app_description': 'A comprehensive Islamic prayer times monitor with system tray integration, desktop widget, and multi-language support.',
        'features': 'Features',
        'feature_list': '• Real-time prayer times calculation\n• System tray notifications\n• Desktop widget\n• Multi-language support (English, French, Arabic)\n• Hijri calendar integration\n• Multiple calculation methods\n• Customizable cities',
        'credits': 'Credits',
        'prayer_calc_credit': 'uses the Prayer Times Calculator algorithm',
        'license': 'License',
        'license_text': 'This software is free to use and distribute.',
        'startup_windows': 'Launch automatically with Windows',
    },
    'fr': {
        'author_name':'Maher Berzig',
        'app_name': 'Moniteur des Heures de Prière',
        'city': 'Ville',
        'method': 'Méthode',
        'add_city': 'Ajouter une ville...',
        'settings': 'Paramètres',
        'desktop_widget': 'Widget de bureau',
        'quit': 'Quitter',
        'next_prayer': 'Prochaine prière',
        'time_remaining': 'Temps restant',
        'close': 'Fermer',
        'timezone': 'Fuseau horaire',
        'dst_active': 'Heure d\'été',
        'time_format': 'Format de l\'heure',
        '24h_format': '24 heures',
        '12h_format': '12 heures (AM/PM)',
        'prayers': {
            'fajr': 'Fajr',
            'sunrise': 'Lever du soleil',
            'dhuhr': 'Dhuhr',
            'asr': 'Asr',
            'maghrib': 'Maghrib',
            'sunset': 'Coucher du soleil',
            'isha': 'Isha'
        },
        'cities': {
            'Mecca': 'la Mecque', 
            'Cairo': 'Le Caire',
            'Algiers': 'Alger',
            'Amman': 'Amman',
            'Baghdad': 'Bagdad',
            'Bahrain': 'Bahreïn',
            'Beirut': 'Beyrouth',
            'Damascus': 'Damas',
            'Doha': 'Doha',
            'Kuwait City': 'Koweït',
            'Muscat': 'Mascate',
            'Riyadh': 'Riyad',
            'Sanaa': 'Sanaa',
            'Tunis': 'Tunis',
            'Abu Dhabi': 'Abou Dhabi',
            'Manama': 'Manama',
            'Ramallah': 'Ramallah'
        },
        'cannot_delete_mecca': 'Impossible de supprimer La Mecque. C\'est une ville protégée.',
        'methods': {
            'MWL': 'Ligue Islamique Mondiale',
            'ISNA': 'Société Islamique d\'Amérique du Nord',
            'Egypt': 'Autorité Générale Égyptienne',
            'Makkah': 'Université Umm al-Qura, La Mecque',
            'Karachi': 'Université des Sciences Islamiques, Karachi'
        },
        'city_name': 'Nom de la ville',
        'latitude': 'Latitude',
        'longitude': 'Longitude',
        'add': 'Ajouter',
        'cancel': 'Annuler',
        'notification_duration': 'Durée de notification (secondes)',
        'show_notification': 'Afficher la notification à l\'heure de prière',
        'language': 'Langue',
        'save': 'Enregistrer',
        'add_new_city': 'Ajouter une nouvelle ville',
        'error': 'Erreur',
        'invalid_coords': 'Coordonnées invalides. Veuillez entrer des nombres valides.',
        'city_exists': 'Cette ville existe déjà.',
        'fetching_location': 'Obtenir la localisation',
        'location_source': 'Source',
        'manual_entry': 'Saisie manuelle',
        'edit_city': 'Modifier la ville...',
        'edit_selected_city': 'Modifier la ville',
        'delete_city': 'Supprimer la ville',
        'city_name_en': 'Nom de la ville (Anglais)',
        'city_name_fr': 'Nom de la ville (Français)',
        'city_name_ar': 'Nom de la ville (Arabe)',
        'restore_defaults': 'Restaurer la configuration par défaut',
        'confirm_restore': 'Confirmer la restauration',
        'confirm_restore_msg': 'Êtes-vous sûr de vouloir restaurer la configuration par défaut? Cela réinitialisera toutes les villes à leurs valeurs d\'origine.',
        'confirm_delete': 'Confirmer la suppression',
        'confirm_delete_msg': 'Êtes-vous sûr de vouloir supprimer cette ville?',
        'cannot_delete_current': 'Impossible de supprimer la ville actuellement sélectionnée. Veuillez d\'abord sélectionner une autre ville.',
        'select_city_to_edit': 'Veuillez d\'abord sélectionner une ville dans le menu Ville.',
        'update': 'Mettre à jour',
        'months': {
            1: 'Janvier', 2: 'Février', 3: 'Mars', 4: 'Avril',
            5: 'Mai', 6: 'Juin', 7: 'Juillet', 8: 'Août',
            9: 'Septembre', 10: 'Octobre', 11: 'Novembre', 12: 'Décembre'
        },
        'hijri_months': {
            1: 'Mouharram', 2: 'Safar', 3: 'Rabia al Awal', 4: 'Rabia al Thani',
            5: 'Joumada al Oula', 6: 'Joumada al Akhira', 7: 'Rajab', 8: 'Chaabane',
            9: 'Ramadan', 10: 'Chawwal', 11: 'Dhou al Qiada', 12: 'Dhou al Hijja'
        },
        'success': 'Succès',
        'location_found': 'Emplacement trouvé avec succès!',
        'city_not_found': 'Ville non trouvée',
        'fetched_from': 'Récupéré de',
        'invalid_response': 'Réponse invalide du service de géocodage',
        'about': 'À propos',
        'about_title': 'À propos du Moniteur des Heures de Prière',
        'version': 'Version',
        'author': 'Auteur',
        'description': 'Description',
        'prayer_calc': 'Calculateur des Heures de Prière',
        'app_description': 'Un moniteur complet des heures de prière islamiques avec intégration de la barre d\'état système, widget de bureau et support multilingue.',
        'features': 'Fonctionnalités',
        'feature_list': '• Calcul en temps réel des heures de prière\n• Notifications de la barre d\'état système\n• Widget de bureau\n• Support multilingue (Anglais, Français, Arabe)\n• Intégration du calendrier Hijri\n• Méthodes de calcul multiples\n• Villes personnalisables',
        'credits': 'Crédits',
        'prayer_calc_credit': 'utilise l\'algorithme Prayer Times Calculator',
        'license': 'Licence',
        'license_text': 'Ce logiciel est libre d\'utilisation et de distribution.',
        'startup_windows': 'Lancer automatiquement avec Windows',
    },
    'ar': {
        'author_name':'ماهر برزيق',
        'app_name': 'مراقب أوقات الصلاة',
        'city': 'المدينة',
        'method': 'الطريقة',
        'add_city': 'إضافة مدينة...',
        'settings': 'الإعدادات',
        'desktop_widget': 'أداة سطح المكتب',
        'quit': 'خروج',
        'next_prayer': 'الصلاة التالية',
        'time_remaining': 'الوقت المتبقي',
        'close': 'إغلاق',
        'timezone': 'المنطقة الزمنية',
        'dst_active': 'التوقيت الصيفي',
        'time_format': 'تنسيق الوقت',
        '24h_format': '24 ساعة',
        '12h_format': '12 ساعة (صباحًا/مساءً)',
        'prayers': {
            'fajr': 'الفجر',
            'sunrise': 'الشروق',
            'dhuhr': 'الظهر',
            'asr': 'العصر',            
            'maghrib': 'المغرب',
            'sunset': 'الغروب',
            'isha': 'العشاء'
        },
        'cities': {
            'Mecca': 'مكة',
            'Cairo': 'القاهرة',
            'Algiers': 'الجزائر',
            'Amman': 'عمّان',
            'Baghdad': 'بغداد',
            'Bahrain': 'البحرين',
            'Beirut': 'بيروت',
            'Damascus': 'دمشق',
            'Doha': 'الدوحة',
            'Kuwait City': 'الكويت',
            'Muscat': 'مسقط',
            'Riyadh': 'الرياض',
            'Sanaa': 'صنعاء',
            'Tunis': 'تونس',
            'Abu Dhabi': 'أبو ظبي',
            'Manama': 'المنامة',
            'Ramallah': 'رام الله'
        },
        'cannot_delete_mecca': 'لا يمكن حذف مكة. هذه مدينة محمية.',
        'methods': {
            'MWL': 'رابطة العالم الإسلامي',
            'ISNA': 'الجمعية الإسلامية لأمريكا الشمالية',
            'Egypt': 'الهيئة المصرية العامة للمساحة',
            'Makkah': 'جامعة أم القرى، مكة',
            'Karachi': 'جامعة العلوم الإسلامية، كراتشي'
        },
        'city_name': 'اسم المدينة',
        'latitude': 'خط العرض',
        'longitude': 'خط الطول',
        'add': 'إضافة',
        'cancel': 'إلغاء',
        'notification_duration': 'مدة الإشعار (ثواني)',
        'show_notification': 'إظهار الإشعار عند وقت الصلاة',
        'language': 'اللغة',
        'save': 'حفظ',
        'add_new_city': 'إضافة مدينة جديدة',
        'error': 'خطأ',
        'invalid_coords': 'إحداثيات غير صالحة. يرجى إدخال أرقام صحيحة.',
        'city_exists': 'هذه المدينة موجودة بالفعل.',
        'fetching_location': 'جلب الموقع',
        'location_source': 'المصدر',
        'manual_entry': 'إدخال يدوي',
        'edit_city': 'تعديل المدينة...',
        'edit_selected_city': 'تعديل المدينة',
        'delete_city': 'حذف المدينة',
        'city_name_en': 'اسم المدينة (بالإنجليزية)',
        'city_name_fr': 'اسم المدينة (بالفرنسية)',
        'city_name_ar': 'اسم المدينة (بالعربية)',
        'restore_defaults': 'استعادة الإعدادات الافتراضية',
        'confirm_restore': 'تأكيد الاستعادة',
        'confirm_restore_msg': 'هل أنت متأكد من استعادة الإعدادات الافتراضية؟ سيؤدي هذا إلى إعادة تعيين جميع المدن إلى قيمها الأصلية.',
        'confirm_delete': 'تأكيد الحذف',
        'confirm_delete_msg': 'هل أنت متأكد من حذف هذه المدينة؟',
        'cannot_delete_current': 'لا يمكن حذف المدينة المحددة حالياً. يرجى اختيار مدينة أخرى أولاً.',
        'select_city_to_edit': 'يرجى اختيار مدينة من قائمة المدينة أولاً.',
        'update': 'تحديث',
        'months': {
            1: '(يناير) جانفي', 2: '(فبراير) فيفري', 3: 'مارس', 4: '(أبريل) أفريل',
            5: '(مايو) ماي', 6: '(يونيو) جوان', 7: '(يوليو) جويلية', 8: '(أغسطس) أوت',
            9: 'سبتمبر', 10: 'أكتوبر', 11: 'نوفمبر', 12: 'ديسمبر'
        },
        'hijri_months': {
            1: 'محرم', 2: 'صفر', 3: 'ربيع الأول', 4: 'ربيع الثاني',
            5: 'جمادى الأولى', 6: 'جمادى الآخرة', 7: 'رجب', 8: 'شعبان',
            9: 'رمضان', 10: 'شوال', 11: 'ذو القعدة', 12: 'ذو الحجة'
        },
        'success': 'نجاح',
        'location_found': 'تم العثور على الموقع بنجاح!',
        'city_not_found': 'لم يتم العثور على المدينة',
        'fetched_from': 'تم الجلب من',
        'invalid_response': 'استجابة غير صالحة من خدمة الترميز الجغرافي',
        'about': 'حول',
        'about_title': 'حول مراقب أوقات الصلاة',
        'version': 'الإصدار',
        'author': 'المؤلف',
        'description': 'الوصف',
        'prayer_calc': 'حاسبة أوقات الصلاة',
        'app_description': 'مراقب شامل لأوقات الصلاة الإسلامية مع تكامل شريط المهام وأداة سطح المكتب ودعم متعدد اللغات.',
        'features': 'الميزات',
        'feature_list': '• حساب أوقات الصلاة في الوقت الفعلي\n• إشعارات شريط المهام\n• أداة سطح المكتب\n• دعم متعدد اللغات (الإنجليزية، الفرنسية، العربية)\n• تكامل التقويم الهجري\n• طرق حساب متعددة\n• مدن قابلة للتخصيص',
        'credits': 'الإسهامات',
        'prayer_calc_credit': 'يستخدم خوارزمية حاسبة أوقات الصلاة',
        'license': 'الترخيص',
        'license_text': 'هذا البرنامج مجاني للاستخدام والتوزيع.',
        'startup_windows': 'التشغيل التلقائي مع ويندوز',
    }
}

# -------------------------------
# Hijri Date Converter (using hijri-converter library)
# -------------------------------
def get_hijri_date(gregorian_date):
    """
    Convert Gregorian date to Hijri date using hijri-converter library
    Returns: (hijri_day, hijri_month, hijri_year) as integers
    """
    try:
        # Convert to Hijri
        hijri = Gregorian(gregorian_date.year, gregorian_date.month, gregorian_date.day).to_hijri()
        return hijri.day, hijri.month, hijri.year
    except Exception as e:
        print(f"Error converting to Hijri: {e}")
        # Return a default value if conversion fails
        return 1, 1, 1445

# -------------------------------
# Configuration Management
# -------------------------------
class Config:
    def __init__(self):
        if os.name == 'nt':  # Windows
            base_dir = os.getenv('APPDATA', os.path.expanduser('~'))
            config_dir = os.path.join(base_dir, 'PrayerTimesMonitor')
        else:  # Linux / macOS
            config_dir = os.path.join(os.path.expanduser('~'), '.config', 'PrayerTimesMonitor')

        os.makedirs(config_dir, exist_ok=True)
        self.config_file = os.path.join(config_dir, 'config.json')
        self.load()
    
    @staticmethod
    def get_default_config():
        """Return the default configuration"""
        return {
            'city': 'Tunis',
            'method': 'Karachi',
            'language': 'en',
            'time_format': '24h',
            'notification_duration': 10,
            'show_notification': True,
            'cities': {
                "Mecca": {
                    "coords": (21.422533 , 39.826203),
                    "timezone": 3,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Mecca", "fr": "la mecque", "ar": "مكة"}
                },
                "Cairo": {
                    "coords": (30.0444, 31.2357),
                    "timezone": 2,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Cairo", "fr": "Le Caire", "ar": "القاهرة"}
                },
                "Algiers": {
                    "coords": (36.7538, 3.0588),
                    "timezone": 1,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Algiers", "fr": "Alger", "ar": "الجزائر"}
                },
                "Amman": {
                    "coords": (31.9454, 35.9284),
                    "timezone": 3,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Amman", "fr": "Amman", "ar": "عمّان"}
                },
                "Baghdad": {
                    "coords": (33.3152, 44.3661),
                    "timezone": 3,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Baghdad", "fr": "Bagdad", "ar": "بغداد"}
                },
                "Bahrain": {
                    "coords": (26.0667, 50.5577),
                    "timezone": 3,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Bahrain", "fr": "Bahreïn", "ar": "البحرين"}
                },
                "Beirut": {
                    "coords": (33.8938, 35.5018),
                    "timezone": 2,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Beirut", "fr": "Beyrouth", "ar": "بيروت"}
                },
                "Damascus": {
                    "coords": (33.5138, 36.2765),
                    "timezone": 2,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Damascus", "fr": "Damas", "ar": "دمشق"}
                },
                "Doha": {
                    "coords": (25.276987, 51.520008),
                    "timezone": 3,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Doha", "fr": "Doha", "ar": "الدوحة"}
                },
                "Kuwait City": {
                    "coords": (29.3759, 47.9774),
                    "timezone": 3,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Kuwait City", "fr": "Koweït", "ar": "الكويت"}
                },
                "Muscat": {
                    "coords": (23.5859, 58.4059),
                    "timezone": 4,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Muscat", "fr": "Mascate", "ar": "مسقط"}
                },
                "Riyadh": {
                    "coords": (24.7136, 46.6753),
                    "timezone": 3,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Riyadh", "fr": "Riyad", "ar": "الرياض"}
                },
                "Sanaa": {
                    "coords": (15.3694, 44.1910),
                    "timezone": 3,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Sanaa", "fr": "Sanaa", "ar": "صنعاء"}
                },
                "Tunis": {
                    "coords": (36.4749, 10.1016),
                    "timezone": 1,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Tunis", "fr": "Tunis", "ar": "تونس"}
                },
                "Abu Dhabi": {
                    "coords": (24.4539, 54.3773),
                    "timezone": 4,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Abu Dhabi", "fr": "Abou Dhabi", "ar": "أبو ظبي"}
                },
                "Manama": {
                    "coords": (26.0275, 50.5505),
                    "timezone": 3,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Manama", "fr": "Manama", "ar": "المنامة"}
                },
                "Ramallah": {
                    "coords": (31.9025, 35.2024),
                    "timezone": 2,
                    "dst": 0,
                    "source": "Built-in",
                    "names": {"en": "Ramallah", "fr": "Ramallah", "ar": "رام الله"}
                }
            }
        }
    
    def load(self):
        default_config = self.get_default_config()
        
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    
                    # Merge loaded config
                    for key in loaded:
                        if key == 'cities':
                            loaded_cities = loaded[key]
                            default_cities = default_config['cities']
                            
                            # Ensure Mecca always exists
                            if "Mecca" not in loaded_cities:
                                loaded_cities["Mecca"] = default_cities["Mecca"]
                            
                            # Ensure all cities have the 'names' field
                            for city_name, city_data in loaded_cities.items():
                                if 'names' not in city_data:
                                    # If it's a built-in city, get from defaults
                                    if city_name in default_cities:
                                        city_data['names'] = default_cities[city_name]['names']
                                    else:
                                        # For user-added cities, use the city name for all languages
                                        city_data['names'] = {
                                            'en': city_name,
                                            'fr': city_name,
                                            'ar': city_name
                                        }
                            
                            default_config['cities'] = loaded_cities
                        else:
                            default_config[key] = loaded[key]
        except Exception as e:
            print(f"Error loading config: {e}")
        
        self.data = default_config
        self.save()            
    
    def restore_defaults(self):
        """Restore default configuration"""
        self.data = self.get_default_config()
        self.save()
    
    def save(self):
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving config: {e}")
    
    def get(self, key, default=None):
        return self.data.get(key, default)
    
    def set(self, key, value):
        self.data[key] = value
        self.save()
# -------------------------------
# Add City Dialog
# -------------------------------
class AddCityDialog(QDialog):
    def __init__(self, existing_cities, language='en', parent=None, edit_mode=False, city_to_edit=None):
        super().__init__(parent)
        self.existing_cities = existing_cities
        self.current_language = language
        self.lang = TRANSLATIONS[language]
        self.result_data = None
        self.edit_mode = edit_mode
        self.city_to_edit = city_to_edit
        self.original_city_name = city_to_edit if edit_mode else None
        
        title = self.lang['edit_selected_city'] if edit_mode else self.lang['add_new_city']
        self.setWindowTitle(title)
        self.setFixedSize(450, 380)
        self.setWindowIcon(QIcon("PrayerTimesMonitor.png"))

        
        if self.current_language == 'ar':
            # set dialog to RTL; this will usually cascade to children/layouts
            self.setLayoutDirection(Qt.RightToLeft)
        else:
            self.setLayoutDirection(Qt.LeftToRight)
        
        
        layout = QFormLayout()
        self.setLayout(layout)

        
        # Three city name inputs for different languages
        self.city_input_en = QLineEdit()
        self.city_input_fr = QLineEdit()
        self.city_input_ar = QLineEdit()
        
        self.lat_input = QLineEdit()
        self.lon_input = QLineEdit()
        
        # Timezone input
        self.timezone_input = QSpinBox()
        self.timezone_input.setRange(-12, 14)
        self.timezone_input.setValue(0)
        self.timezone_input.setPrefix("UTC ")
        
        # DST checkbox
        self.dst_checkbox = QCheckBox(self.lang['dst_active'])
        self.dst_checkbox.setChecked(False)
        
        self.source_label = QLabel(self.lang['manual_entry'])
        
        # If editing, populate fields
        if edit_mode and city_to_edit and city_to_edit in existing_cities:
            city_data = existing_cities[city_to_edit]
            
            # Get city names
            if 'names' in city_data:
                names = city_data['names']
                self.city_input_en.setText(names.get('en', city_to_edit))
                self.city_input_fr.setText(names.get('fr', city_to_edit))
                self.city_input_ar.setText(names.get('ar', city_to_edit))
            else:
                # Fallback for old format
                self.city_input_en.setText(city_to_edit)
                self.city_input_fr.setText(city_to_edit)
                self.city_input_ar.setText(city_to_edit)
            
            # Populate other fields
            coords = city_data['coords']
            self.lat_input.setText(str(coords[0]))
            self.lon_input.setText(str(coords[1]))
            self.timezone_input.setValue(city_data.get('timezone', 0))
            self.dst_checkbox.setChecked(city_data.get('dst', 0) == 1)
            self.source_label.setText(city_data.get('source', self.lang['manual_entry']))
        
        layout.addRow(self.lang['city_name_en'] + ":", self.city_input_en)
        layout.addRow(self.lang['city_name_fr'] + ":", self.city_input_fr)
        layout.addRow(self.lang['city_name_ar'] + ":", self.city_input_ar)
        layout.addRow(self.lang['latitude'] + ":", self.lat_input)
        layout.addRow(self.lang['longitude'] + ":", self.lon_input)
        layout.addRow(self.lang['timezone'] + ":", self.timezone_input)
        layout.addRow("", self.dst_checkbox)
        layout.addRow(self.lang['location_source'] + ":", self.source_label)
        
        btn_layout = QHBoxLayout()
        btn_fetch = QPushButton(self.lang['fetching_location'])
        btn_fetch.clicked.connect(self.fetch_location)
        
        btn_text = self.lang['update'] if edit_mode else self.lang['add']
        btn_add = QPushButton(btn_text)
        btn_add.clicked.connect(self.accept_city)
        
        btn_cancel = QPushButton(self.lang['cancel'])
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_fetch)
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(btn_cancel)
        layout.addRow(btn_layout)
    
    def update_language(self, language):
        """Update dialog language without recreating it"""
        if self.current_language == language:
            return
        
        self.current_language = language
        self.lang = TRANSLATIONS[language]
        
        title = self.lang['edit_selected_city'] if self.edit_mode else self.lang['add_new_city']
        self.setWindowTitle(title)
        
        layout = self.layout()
        layout.labelForField(self.city_input_en).setText(self.lang['city_name_en'] + ":")
        layout.labelForField(self.city_input_fr).setText(self.lang['city_name_fr'] + ":")
        layout.labelForField(self.city_input_ar).setText(self.lang['city_name_ar'] + ":")
        layout.labelForField(self.lat_input).setText(self.lang['latitude'] + ":")
        layout.labelForField(self.lon_input).setText(self.lang['longitude'] + ":")
        layout.labelForField(self.timezone_input).setText(self.lang['timezone'] + ":")
        
        self.dst_checkbox.setText(self.lang['dst_active'])
        
        if self.source_label.text() in ['Manual Entry', 'Saisie manuelle', 'إدخال يدوي']:
            self.source_label.setText(self.lang['manual_entry'])
        
        btn_layout = layout.itemAt(layout.rowCount() - 1).layout()
        if btn_layout:
            btn_layout.itemAt(0).widget().setText(self.lang['fetching_location'])
            btn_text = self.lang['update'] if self.edit_mode else self.lang['add']
            btn_layout.itemAt(1).widget().setText(btn_text)
            btn_layout.itemAt(2).widget().setText(self.lang['cancel'])
    
    
    def accept_city(self):
        city_en = self.city_input_en.text().strip()
        city_fr = self.city_input_fr.text().strip()
        city_ar = self.city_input_ar.text().strip()
        
        if not city_en:
            QMessageBox.warning(self, self.lang['error'], "English city name is required.")
            return
        
        # Use English name as the primary key
        city_key = city_en
        
        try:
            lat = float(self.lat_input.text().strip())
            lon = float(self.lon_input.text().strip())
            timezone = self.timezone_input.value()
            dst = 1 if self.dst_checkbox.isChecked() else 0
        except ValueError:
            QMessageBox.warning(self, self.lang['error'], self.lang['invalid_coords'])
            return
        
        # Check if city exists (only if not editing or if name changed)
        if not self.edit_mode or (self.edit_mode and city_key != self.original_city_name):
            if city_key in self.existing_cities:
                QMessageBox.warning(self, self.lang['error'], self.lang['city_exists'])
                return
        
        source = self.source_label.text() if self.source_label.text() else self.lang['manual_entry']
        
        self.result_data = {
            'name': city_key,
            'names': {
                'en': city_en if city_en else city_key,
                'fr': city_fr if city_fr else city_key,
                'ar': city_ar if city_ar else city_key
            },
            'coords': (lat, lon),
            'timezone': timezone,
            'dst': dst,
            'source': source,
            'is_edit': self.edit_mode,
            'original_name': self.original_city_name
        }
        self.accept()
    def fetch_location(self):
        """Fetch city coordinates from any language input"""
        # Get all three city names
        city_en = self.city_input_en.text().strip()
        city_fr = self.city_input_fr.text().strip()
        city_ar = self.city_input_ar.text().strip()
        
        # Try with whichever field has input (priority: English, French, Arabic)
        city_query = city_en or city_fr or city_ar
        
        if not city_query:
            QMessageBox.warning(self, self.lang['error'], "Please enter a city name in at least one language.")
            return
        
        try:
            # Use Nominatim OpenStreetMap API which supports multiple languages
            url = "https://nominatim.openstreetmap.org/search"
            headers = {
                'User-Agent': 'PrayerTimesApp/1.0'
            }
            params = {
                'q': city_query,
                'format': 'json',
                'limit': 1,
                'addressdetails': 1
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if not data:
                # Try with alternative geocoding service (Google-style format)
                self.try_alternative_geocoding(city_query)
                return
            
            result = data[0]
            lat = float(result['lat'])
            lon = float(result['lon'])
            
            # Get the place details
            display_name = result.get('display_name', '')
            address = result.get('address', {})
            
            # Extract city name in different languages from the result
            city_name = (
                address.get('city') or 
                address.get('town') or 
                address.get('village') or 
                address.get('municipality') or
                result.get('name', city_query)
            )
            
            # Update all three fields if they're empty
            if not city_en:
                self.city_input_en.setText(city_name)
            if not city_fr:
                self.city_input_fr.setText(city_name)
            if not city_ar:
                self.city_input_ar.setText(city_name)
            
            # Set coordinates
            self.lat_input.setText(f"{lat:.6f}")
            self.lon_input.setText(f"{lon:.6f}")
            
            # Try to determine timezone
            timezone_offset = self.estimate_timezone(lon)
            self.timezone_input.setValue(timezone_offset)
            
            # Update source label
            self.source_label.setText(f"{self.lang['fetched_from']}: OpenStreetMap")
            
            # Now fetch translations for all languages
            self.fetch_city_translations(city_name, lat, lon)
            
            QMessageBox.information(
                self, 
                self.lang['success'], 
                f"{self.lang['location_found']}\n\n{display_name}\n\nLat: {lat:.6f}, Lon: {lon:.6f}"
            )
            
        except requests.RequestException as e:
            QMessageBox.warning(
                self, 
                self.lang['error'], 
                f"{self.lang['fetch_failed']}\n\nError: {str(e)}"
            )
        except (KeyError, ValueError, IndexError) as e:
            QMessageBox.warning(
                self, 
                self.lang['error'], 
                f"{self.lang['invalid_response']}\n\nError: {str(e)}"
            )

    def try_alternative_geocoding(self, city_query):
        """Try alternative geocoding service if primary fails"""
        try:
            # Use geocode.xyz as alternative (supports multiple languages)
            url = "https://geocode.xyz"
            params = {
                'locate': city_query,
                'json': 1
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if 'latt' in data and 'longt' in data:
                lat = float(data['latt'])
                lon = float(data['longt'])
                
                # Get city name
                city_name = data.get('standard', {}).get('city', city_query)
                
                # Update fields
                if not self.city_input_en.text().strip():
                    self.city_input_en.setText(city_name)
                if not self.city_input_fr.text().strip():
                    self.city_input_fr.setText(city_name)
                if not self.city_input_ar.text().strip():
                    self.city_input_ar.setText(city_name)
                
                self.lat_input.setText(f"{lat:.6f}")
                self.lon_input.setText(f"{lon:.6f}")
                
                timezone_offset = self.estimate_timezone(lon)
                self.timezone_input.setValue(timezone_offset)
                
                self.source_label.setText(f"{self.lang['fetched_from']}: geocode.xyz")
                
                # Fetch translations
                self.fetch_city_translations(city_name, lat, lon)
                
                QMessageBox.information(
                    self, 
                    self.lang['success'], 
                    f"{self.lang['location_found']}\n\n{city_name}\n\nLat: {lat:.6f}, Lon: {lon:.6f}"
                )
            else:
                raise ValueError("Location not found")
                
        except Exception as e:
            QMessageBox.warning(
                self, 
                self.lang['error'], 
                f"{self.lang['city_not_found']}\n\n{city_query}\n\nError: {str(e)}"
            )

    def fetch_city_translations(self, city_name, lat, lon):
        """Fetch city name translations in multiple languages"""
        try:
            # Use reverse geocoding with Nominatim to get translations
            url = "https://nominatim.openstreetmap.org/reverse"
            headers = {
                'User-Agent': 'PrayerTimesApp/1.0'
            }
            
            # Fetch English version
            if not self.city_input_en.text().strip():
                params = {
                    'lat': lat,
                    'lon': lon,
                    'format': 'json',
                    'addressdetails': 1,
                    'accept-language': 'en'
                }
                response = requests.get(url, params=params, headers=headers, timeout=5)
                if response.ok:
                    data = response.json()
                    address = data.get('address', {})
                    city_en = (
                        address.get('city') or 
                        address.get('town') or 
                        address.get('village') or 
                        address.get('municipality') or
                        city_name
                    )
                    self.city_input_en.setText(city_en)
            
            # Fetch French version
            if not self.city_input_fr.text().strip():
                params = {
                    'lat': lat,
                    'lon': lon,
                    'format': 'json',
                    'addressdetails': 1,
                    'accept-language': 'fr'
                }
                response = requests.get(url, params=params, headers=headers, timeout=5)
                if response.ok:
                    data = response.json()
                    address = data.get('address', {})
                    city_fr = (
                        address.get('city') or 
                        address.get('town') or 
                        address.get('village') or 
                        address.get('municipality') or
                        city_name
                    )
                    self.city_input_fr.setText(city_fr)
            
            # Fetch Arabic version
            if not self.city_input_ar.text().strip():
                params = {
                    'lat': lat,
                    'lon': lon,
                    'format': 'json',
                    'addressdetails': 1,
                    'accept-language': 'ar'
                }
                response = requests.get(url, params=params, headers=headers, timeout=5)
                if response.ok:
                    data = response.json()
                    address = data.get('address', {})
                    city_ar = (
                        address.get('city') or 
                        address.get('town') or 
                        address.get('village') or 
                        address.get('municipality') or
                        city_name
                    )
                    self.city_input_ar.setText(city_ar)
                    
        except Exception as e:
            # Silently fail for translations - not critical
            print(f"Could not fetch translations: {e}")

    def estimate_timezone(self, lon):
        """Estimate timezone offset based on longitude"""
        # Simple estimation: timezone ≈ longitude / 15
        # This is approximate but works for most cases
        estimated = round(lon / 15)
        
        # Clamp between -12 and +14
        return max(-12, min(14, estimated))
    

# -------------------------------
# Settings Dialog
# -------------------------------
class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.lang = TRANSLATIONS[config.get('language', 'en')]
        self.current_language = config.get('language', 'en')
        
        self.setWindowTitle(self.lang['settings'])
        self.setFixedSize(400, 390)
        self.setWindowIcon(QIcon("PrayerTimesMonitor.png"))
        
        if self.current_language == 'ar':
            # set dialog to RTL; this will usually cascade to children/layouts
            self.setLayoutDirection(Qt.RightToLeft)
        else:
            self.setLayoutDirection(Qt.LeftToRight)
        
        layout = QFormLayout()
        self.setLayout(layout)
        
        # Language selector
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(['English', 'Français', 'العربية'])
        lang_map = {'en': 0, 'fr': 1, 'ar': 2}
        self.lang_combo.setCurrentIndex(lang_map.get(config.get('language', 'en'), 0))
        
        # Time format selector
        self.time_format_combo = QComboBox()
        self.time_format_combo.addItem(self.lang['24h_format'], '24h')
        self.time_format_combo.addItem(self.lang['12h_format'], '12h')
        current_format = config.get('time_format', '24h')
        self.time_format_combo.setCurrentIndex(0 if current_format == '24h' else 1)
        
        # Notification duration
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(0, 300)
        self.duration_spin.setValue(config.get('notification_duration', 10))
        self.duration_spin.setSuffix(' s')
        
        # Show notification checkbox
        self.show_notif_check = QCheckBox()
        self.show_notif_check.setChecked(config.get('show_notification', True))
        
        layout.addRow(self.lang['language'] + ":", self.lang_combo)
        layout.addRow(self.lang['time_format'] + ":", self.time_format_combo)
        layout.addRow(self.lang['notification_duration'] + ":", self.duration_spin)
        layout.addRow(self.lang['show_notification'] + ":", self.show_notif_check)

        # ── Windows startup ───────────────────────────────────────────────────
        if sys.platform == "win32":
            self.startup_check = QCheckBox(self.lang.get('startup_windows',
                                           'Launch automatically with Windows'))
            self.startup_check.setChecked(get_startup_enabled())
            layout.addRow(self.startup_check)
        # ─────────────────────────────────────────────────────────────────────

        # Add separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addRow(separator)
        
        
        # Save and Cancel buttons
        btn_layout = QHBoxLayout()
        btn_save = QPushButton(self.lang['save'])
        btn_save.clicked.connect(self.save_settings)
        btn_cancel = QPushButton(self.lang['cancel'])
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addRow(btn_layout)
        
        # Restore defaults button
        btn_restore = QPushButton(self.lang['restore_defaults'])
        btn_restore.clicked.connect(self.restore_defaults)
        layout.addRow(btn_restore)
        # Set a fixed width that fits the text
        text = "Restorer la configuration par défaut"
        fm = btn_restore.fontMetrics()
        btn_restore.setFixedWidth(fm.horizontalAdvance(text) + 40)  # +40 for padding

    
    def restore_defaults(self):
        """Restore default configuration"""
        reply = QMessageBox.question(
            self,
            self.lang['confirm_restore'],
            self.lang['confirm_restore_msg'],
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.config.restore_defaults()
            QMessageBox.information(self, self.lang['settings'], "Configuration restored to defaults.")
            self.accept()
    
    def save_settings(self):
        lang_map = {0: 'en', 1: 'fr', 2: 'ar'}
        self.config.set('language', lang_map[self.lang_combo.currentIndex()])
        self.config.set('time_format', self.time_format_combo.currentData())
        self.config.set('notification_duration', self.duration_spin.value())
        self.config.set('show_notification', self.show_notif_check.isChecked())

        # ── Windows startup ───────────────────────────────────────────────────
        if sys.platform == "win32" and hasattr(self, 'startup_check'):
            set_startup_enabled(self.startup_check.isChecked())
        # ─────────────────────────────────────────────────────────────────────

        self.accept()
     

# -------------------------------
# Desktop Widget
# -------------------------------
class DesktopWidget(QWidget):
    def __init__(self, config, parent_app):
        super().__init__()
        self.config = config
        self.parent_app = parent_app
        
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        self.setFixedSize(400, 500)
        
        # Timer for updates
        self.timer = QTimer()
        self.timer.timeout.connect(self.update)
        self.timer.start(1000)
        
        # Position in bottom-right corner
        screen = QApplication.desktop().screenGeometry()
        self.move(screen.width() - self.width() - 20, screen.height() - self.height() - 60)
    
    def format_date(self, dt):
        """Format date according to current language (Gregorian only)"""
        lang = TRANSLATIONS[self.config.get('language', 'en')]
        current_lang = self.config.get('language', 'en')
        
        RLM = "\u200F"  # Right-to-Left Mark
        LRM = "\u200E"  # Left-to-Right Mark
        
        day = dt.day
        month_name = lang['months'][dt.month]
        year = dt.year
        
        # Format based on language convention (all use Western numerals)
        if current_lang == 'ar':
            # Arabic: Year Month Day with proper RTL/LTR marks
            return f"{LRM}{year} {RLM}{month_name} {LRM}{day}"
        elif current_lang == 'fr':
            # French: Day Month Year (e.g., "15 Janvier 2024")
            return f"{day} {month_name} {year}"
        else:
            # English: Month Day, Year (e.g., "January 15, 2024")
            return f"{month_name} {day}, {year}"
        
    def format_hijri_date(self, dt):
        """Format Hijri date with current language translation"""
        hijri_day, hijri_month, hijri_year = get_hijri_date(dt)
        current_lang = self.config.get('language', 'en')
        lang = TRANSLATIONS[current_lang]
        
        RLM = "\u200F"  # Right-to-Left Mark
        LRM = "\u200E"  # Left-to-Right Mark
        
        if 'hijri_months' in lang:
            month_name = lang['hijri_months'].get(hijri_month, '')
            
            # Format based on language
            if current_lang == 'ar':
                # Arabic: Year Month Day with proper RTL/LTR marks
                return f"{LRM}{hijri_year} {RLM}{month_name} {LRM}{hijri_day}"
            elif current_lang == 'fr':
                # French: Day Month Year
                return f"{hijri_day} {month_name} {hijri_year}"
            else:
                # English: Month Day, Year
                return f"{month_name} {hijri_day}, {hijri_year}"
        return ""
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        lang = TRANSLATIONS[self.config.get('language', 'en')]
        current_lang = self.config.get('language', 'en')
        
        # Background
        painter.setBrush(QColor(40, 40, 40, 220))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.rect(), 15, 15)
        
        # Center alignment
        width = self.width()
        
        # Current time - centered
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont('Arial', 36, QFont.Bold))
        current_time = QTime.currentTime().toString('HH:mm:ss')
        time_rect = painter.fontMetrics().boundingRect(current_time)
        painter.drawText((width - time_rect.width()) // 2, 60, current_time)
        
        # Current date - centered with translated month
        now = datetime.now()
        current_date = self.format_date(now)
        
        # For Arabic, use appropriate font for month names
        if current_lang == 'ar':
            painter.setFont(QFont('Traditional Arabic', 14))
        else:
            painter.setFont(QFont('Arial', 14))
        
        date_rect = painter.fontMetrics().boundingRect(current_date)
        painter.drawText((width - date_rect.width()) // 2, 90, current_date)
        
        # Add Hijri date (shown for all languages now with translations)
        hijri_date = self.format_hijri_date(now)
        if hijri_date:
            if current_lang == 'ar':
                painter.setFont(QFont('Traditional Arabic', 12))
            else:
                painter.setFont(QFont('Arial', 12))
            
            painter.setPen(QColor(180, 180, 180))
            hijri_rect = painter.fontMetrics().boundingRect(hijri_date)
            painter.drawText((width - hijri_rect.width()) // 2, 108, hijri_date)
            separator_y = 120
        else:
            separator_y = 110
        
        # Reset font and color for rest of the content
        painter.setFont(QFont('Arial', 14))
        painter.setPen(QColor(255, 255, 255))
        
        # Separator line
        painter.setPen(QColor(100, 100, 100))
        painter.drawLine(40, separator_y, width - 40, separator_y)
        
        # Adjust vertical positions if Hijri date is shown
        next_prayer_y = 135 if not hijri_date else 145
        countdown_y = 175 if not hijri_date else 185
        time_y = 195 if not hijri_date else 205
        separator2_y = 210 if not hijri_date else 220
        prayers_start_y = 245 if not hijri_date else 255
        
        # Next prayer info
        if hasattr(self.parent_app, 'times'):
            now_datetime = datetime.now()
            future = [(p, t) for p, t in self.parent_app.times.items() if t > now_datetime]
            if not future:
                next_prayer, next_time = list(self.parent_app.times.items())[0]
                next_time += timedelta(days=1)
            else:
                next_prayer, next_time = future[0]
            
            remaining = next_time - now_datetime
            h, m, s = int(remaining.total_seconds() // 3600), int((remaining.total_seconds() % 3600) // 60), int(remaining.total_seconds() % 60)
            
            # Next prayer label - centered
            painter.setFont(QFont('Arial', 12, QFont.Bold))
            painter.setPen(QColor(100, 200, 255))
            prayer_name = lang['prayers'].get(next_prayer, next_prayer)
            next_prayer_text = f"{lang['next_prayer']}: {prayer_name}"
            next_prayer_rect = painter.fontMetrics().boundingRect(next_prayer_text)
            painter.drawText((width - next_prayer_rect.width()) // 2, next_prayer_y, next_prayer_text)
            
            # Countdown - centered
            painter.setFont(QFont('Arial', 28, QFont.Bold))
            painter.setPen(QColor(100, 255, 100))
            countdown = f"{h:02d}:{m:02d}:{s:02d}"
            countdown_rect = painter.fontMetrics().boundingRect(countdown)
            painter.drawText((width - countdown_rect.width()) // 2, countdown_y, countdown)
            
            # Prayer time - centered
            painter.setFont(QFont('Arial', 12))
            painter.setPen(QColor(200, 200, 200))
            time_text = self.parent_app.format_time(next_time)
            time_rect = painter.fontMetrics().boundingRect(time_text)
            painter.drawText((width - time_rect.width()) // 2, time_y, time_text)
            
            # Separator line
            painter.setPen(QColor(100, 100, 100))
            painter.drawLine(40, separator2_y, width - 40, separator2_y)
            
            # All prayer times with icons - centered
            prayer_icons = {
                'fajr': '🌙',
                'sunrise': '🌅',
                'dhuhr': '☀️',
                'asr': '🌤️',
                'maghrib': '🌇',
                'sunset': '🌆',
                'isha': '🌙'
            }
            
            painter.setFont(QFont('Arial', 14))
            y_pos = prayers_start_y
            
            for prayer, time in self.parent_app.times.items():
                icon = prayer_icons.get(prayer, '🕌')
                prayer_name = lang['prayers'].get(prayer, prayer)
                # Use format_time helper
                time_str = self.parent_app.format_time(time)
                
                # Highlight next prayer
                if prayer == next_prayer:
                    painter.setPen(QColor(100, 255, 100))
                    painter.setFont(QFont('Arial', 14, QFont.Bold))
                else:
                    painter.setPen(QColor(220, 220, 220))
                    painter.setFont(QFont('Arial', 13))
                    
                if  prayer == 'sunrise':
                    sunrise_name = prayer_name
                    time_sunrise = time_str
                    continue
                    
                if  prayer == 'sunset':
                    sunset_name = prayer_name
                    time_sunset = time_str
                    continue
    

                text = f"{icon} {prayer_name}: {time_str}"
                text_rect = painter.fontMetrics().boundingRect(text)
                painter.drawText((width - text_rect.width()) // 2, y_pos, text)
                y_pos += 35
                        
            # Separator line
            painter.setPen(QColor(100, 100, 100))
            painter.drawLine(40, y_pos-20, width - 40, y_pos-20)
            y_pos += 10
            
            painter.setPen(QColor(220, 220, 220))
            painter.setFont(QFont('Arial', 13))

            text = f"{icon} {sunrise_name}: {time_sunrise}"
            text_rect = painter.fontMetrics().boundingRect(text)
            painter.drawText((width - text_rect.width()) // 2, y_pos, text)
            y_pos += 35
            
            text = f"{icon} {sunset_name}: {time_sunset}"
            text_rect = painter.fontMetrics().boundingRect(text)
            painter.drawText((width - text_rect.width()) // 2, y_pos, text)
            y_pos += 35
            
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
    
    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self.drag_position)
            event.accept()
# -------------------------------
# Prayer Notification
# -------------------------------
class PrayerNotification(QWidget):
    def __init__(self, city, prayer, language='en'):
        super().__init__()
        lang = TRANSLATIONS[language]
        
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.ToolTip)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # Create frame
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: rgba(40, 40, 40, 240);
                border: 2px solid #4CAF50;
                border-radius: 10px;
            }
        """)
        frame_layout = QVBoxLayout()
        frame.setLayout(frame_layout)
        
        prayer_name = lang['prayers'].get(prayer, prayer)
        if prayer == 'sunset' and language == 'ar':
            prayer_name = 'المغرب'
        lbl = QLabel(f"🕌 {prayer_name}")
        lbl.setStyleSheet("color: #4CAF50; font-size: 14px;font-weight: bold; padding: 10px;")
        lbl.setAlignment(Qt.AlignCenter)
        frame_layout.addWidget(lbl)
        
        
        translated_city = lang.get('cities', {}).get(city, city)
        city_lbl = QLabel(f"{translated_city}")
        #city_lbl = QLabel(city)
        city_lbl.setStyleSheet("color: white; font-size: 16px; padding: 10px;")
        city_lbl.setAlignment(Qt.AlignCenter)
        frame_layout.addWidget(city_lbl)
        
        verse = QLabel("إِنَّ الصَّلَاةَ كَانَتْ عَلَى الْمُؤْمِنِينَ كِتَابًا مَّوْقُوتًا")
        verse.setStyleSheet("color: #FFD700; font-size: 28px; padding: 10px; font-family: 'Traditional Arabic', 'Arabic Typesetting';")
        verse.setAlignment(Qt.AlignCenter)
        frame_layout.addWidget(verse)
        
        layout.addWidget(frame)
        
        self.setFixedSize(400, 200)
        
        # Position near system tray
        screen = QApplication.desktop().screenGeometry()
        self.move(screen.width() - self.width() - 20, screen.height() - self.height() - 60)

# -------------------------------
# Hover Dialog for Tray Icon
# -------------------------------
class HoverDialog(QWidget):
    def __init__(self, parent_app):
        super().__init__()
        self.parent_app = parent_app
        
        #self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.ToolTip)
        #self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Popup)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)
        
        # Create frame
        self.frame = QFrame()
        self.frame.setStyleSheet("""
            QFrame {
                background-color: rgba(30, 30, 30, 250);
                border: 2px solid #2196F3;
                border-radius: 12px;
            }
        """)
        self.frame_layout = QVBoxLayout()
        self.frame.setLayout(self.frame_layout)
        
        self.main_layout.addWidget(self.frame)
        
        self.setFixedSize(350, 550)
# Add this method to handle mouse clicks on the dialog
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.close()
            event.accept()
        else:
            super().mousePressEvent(event)
            
    def update_content(self):
        # Clear existing widgets
        while self.frame_layout.count():
            child = self.frame_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        if not hasattr(self.parent_app, 'times') or not self.parent_app.times:
            return
        
        lang = TRANSLATIONS[self.parent_app.config.get('language', 'en')]
        
        # City and method
        translated_city = lang.get('cities', {}).get(self.parent_app.city, self.parent_app.city)
        city_label = QLabel(f"📍 {translated_city}")
        city_label.setStyleSheet("color: #2196F3; font-size: 18px; font-weight: bold; padding: 10px;")
        city_label.setAlignment(Qt.AlignCenter)
        self.frame_layout.addWidget(city_label)
        
        translated_method = lang.get('methods', {}).get(self.parent_app.method, self.parent_app.method)
        method_label = QLabel(f"📖 {translated_method}")
        method_label.setStyleSheet("color: #FFA726; font-size: 11px; padding: 5px;")
        method_label.setAlignment(Qt.AlignCenter)
        self.frame_layout.addWidget(method_label)
        
        # Next prayer
        now = datetime.now()
        future = [(p, t) for p, t in self.parent_app.times.items() if t > now]
        if not future:
            next_prayer, next_time = list(self.parent_app.times.items())[0]
            next_time += timedelta(days=1)
        else:
            next_prayer, next_time = future[0]
        
        remaining = next_time - now
        h, m, s = int(remaining.total_seconds() // 3600), int((remaining.total_seconds() % 3600) // 60), int(remaining.total_seconds() % 60)
        
        next_label = QLabel(f"⏰ {lang['next_prayer']}")
        next_label.setStyleSheet("color: #4CAF50; font-size: 14px; font-weight: bold; padding: 10px 10px 5px 10px;")
        next_label.setAlignment(Qt.AlignCenter)
        self.frame_layout.addWidget(next_label)
        
        prayer_name = lang['prayers'].get(next_prayer, next_prayer)
        prayer_label = QLabel(prayer_name)
        prayer_label.setStyleSheet("color: white; font-size: 20px; font-weight: bold; padding: 0px;")
        prayer_label.setAlignment(Qt.AlignCenter)
        self.frame_layout.addWidget(prayer_label)
        
        time_label = QLabel(f"{h:02d}:{m:02d}:{s:02d}")
        time_label.setStyleSheet("color: #FFD700; font-size: 28px; font-weight: bold; padding: 10px;")
        time_label.setAlignment(Qt.AlignCenter)
        self.frame_layout.addWidget(time_label)
        
        # # Separator
        # separator = QFrame()
        # separator.setFrameShape(QFrame.HLine)
        # separator.setStyleSheet("background-color: #555;")
        # self.frame_layout.addWidget(separator)
        
        # All prayer times with icons
        prayer_icons = {
            'fajr': '🌙',
            'sunrise': '🌅',
            'dhuhr': '☀️',
            'asr': '🌤️',
            'maghrib': '🌇',
            'sunset': '🌆',
            'isha': '🌙'
        }



        
        for prayer, time in self.parent_app.times.items():
            icon = prayer_icons.get(prayer, '🕌')
            prayer_name = lang['prayers'].get(prayer, prayer)
            # Use the format_time helper method
            time_str = self.parent_app.format_time(time)
            
            # Highlight current/next prayer
            if prayer == next_prayer:
                style = "color: #4CAF50; font-size: 15px; font-weight: bold; padding: 3px 15px;"
            else:
                style = "color: #BDBDBD; font-size: 13px; padding: 3px 15px;"
            if  prayer == 'sunrise':
                sunrise_name = prayer_name
                time_sunrise = time_str
                continue
            if  prayer == 'sunset':
                sunset_name = prayer_name
                time_sunset = time_str
                continue
            
            prayer_time_label = QLabel(f"{icon} {prayer_name}: {time_str}")
            prayer_time_label.setStyleSheet(style)
            prayer_time_label.setAlignment(Qt.AlignCenter)
            self.frame_layout.addWidget(prayer_time_label)
            
       
        
        sun_frame = QFrame()
        sun_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(30, 30, 30, 250);
                border: 2px solid #2196F3;
                border-radius: 12px;
                padding: 0px;
            }
            QLabel {
                color: #BDBDBD;
                font-size: 13px;
                background: transparent;
                border: none;
            }
        """)


        sun_layout = QVBoxLayout()   # or QHBoxLayout for side-by-side
        sun_frame.setLayout(sun_layout)

        # Sunrise label
        sunrise_label = QLabel(f"{icon} {sunrise_name}: {time_sunrise}")
        sunrise_label.setAlignment(Qt.AlignCenter)

        # Sunset label
        sunset_label = QLabel(f"{icon} {sunset_name}: {time_sunset}")
        sunset_label.setAlignment(Qt.AlignCenter)

        # Add both labels to same frame
        sun_layout.addWidget(sunrise_label)
        sun_layout.addWidget(sunset_label)

        # Add the frame to your main layout
        self.frame_layout.addWidget(sun_frame)

       
    
    def show_near_tray(self):
        # Get screen geometry
        screen = QApplication.desktop().screenGeometry()
        
        # Get cursor position
        cursor_pos = QApplication.desktop().cursor().pos()
        
        # Position dialog above the taskbar
        x = cursor_pos.x() - self.width() // 2
        y = screen.height() - self.height() - 80  # Above taskbar
        
        # Keep within screen bounds
        if x < 0:
            x = 10
        elif x + self.width() > screen.width():
            x = screen.width() - self.width() - 10
        
        self.move(x, y)
        self.update_content()
        self.show()
# -------------------------------
# About Dialog
# -------------------------------
class AboutDialog(QDialog):
    def __init__(self, language='en', parent=None):
        super().__init__(parent)
        self.lang = TRANSLATIONS[language]
        
        self.setWindowTitle(self.lang['about_title'])
        self.setFixedSize(500, 650)
        self.setWindowIcon(QIcon("PrayerTimesMonitor.png"))
        
        # Main layout
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)
        
        # Scroll area for content
        scroll = QWidget()
        scroll_layout = QVBoxLayout()
        scroll.setLayout(scroll_layout)
        
        # App icon/title
        title_label = QLabel("Prayer Times Monitor")
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #2196F3; padding: 6px;")
        title_label.setAlignment(Qt.AlignCenter)
        scroll_layout.addWidget(title_label)
        
        # Version
        version_label = QLabel(f"{self.lang['version']}: 1.0.0")
        version_label.setStyleSheet("font-size: 14px; color: #666; padding: 5px;")
        version_label.setAlignment(Qt.AlignCenter)
        scroll_layout.addWidget(version_label)
        
        # Separator
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.HLine)
        separator1.setFrameShadow(QFrame.Sunken)
        scroll_layout.addWidget(separator1)
        
        # Author
        author_section = QLabel(f"<b>{self.lang['author']}:</b>")
        author_section.setStyleSheet("font-size: 13px; padding: 10px 10px 5px 10px;")
        scroll_layout.addWidget(author_section)
        
        author_label = QLabel(self.lang['author_name'])  # Replace with your name
        author_label.setStyleSheet("font-size: 12px; color: #444; padding: 0px 20px 10px 20px;")
        scroll_layout.addWidget(author_label)
        
        # Description
        desc_section = QLabel(f"<b>{self.lang['description']}:</b>")
        desc_section.setStyleSheet("font-size: 13px; padding: 10px 10px 5px 10px;")
        scroll_layout.addWidget(desc_section)
        
        desc_label = QLabel(self.lang['app_description'])
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("font-size: 12px; color: #444; padding: 0px 20px 10px 20px;")
        scroll_layout.addWidget(desc_label)
        
        # Features
        features_section = QLabel(f"<b>{self.lang['features']}:</b>")
        features_section.setStyleSheet("font-size: 13px; padding: 10px 10px 5px 10px;")
        scroll_layout.addWidget(features_section)
        
        features_label = QLabel(self.lang['feature_list'])
        features_label.setWordWrap(True)
        features_label.setStyleSheet("font-size: 11px; color: #444; padding: 0px 20px 10px 20px;")
        scroll_layout.addWidget(features_label)
        
        # Separator
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.HLine)
        separator2.setFrameShadow(QFrame.Sunken)
        scroll_layout.addWidget(separator2)
        
        # Credits
        credits_section = QLabel(f"<b>{self.lang['credits']}:</b>")
        credits_section.setStyleSheet("font-size: 13px; padding: 10px 10px 5px 10px;")
        scroll_layout.addWidget(credits_section)
        
        # Prayer Times Calculator credit with clickable link
        prayer_calc_label = QLabel(
            f"{self.lang['prayer_calc']} <b>(ver 1.0)</b> {self.lang['prayer_calc_credit']}<br>"
            f'<a href="http://praytimes.org" style="color: #2196F3;">PrayTimes.org</a>'
        )
        prayer_calc_label.setOpenExternalLinks(True)
        prayer_calc_label.setWordWrap(True)
        prayer_calc_label.setStyleSheet("font-size: 11px; color: #444; padding: 0px 20px 10px 20px;")
        scroll_layout.addWidget(prayer_calc_label)
        
        # Other credits
        other_credits = QLabel(
            "• hijri-converter library for Hijri date conversions<br>"
            "• PyQt5 for the graphical interface<br>"
            "• OpenStreetMap Nominatim for geocoding services"
        )
        other_credits.setWordWrap(True)
        other_credits.setStyleSheet("font-size: 11px; color: #444; padding: 0px 20px 10px 20px;")
        scroll_layout.addWidget(other_credits)
        
        # Separator
        separator3 = QFrame()
        separator3.setFrameShape(QFrame.HLine)
        separator3.setFrameShadow(QFrame.Sunken)
        scroll_layout.addWidget(separator3)
        
        # License
        license_section = QLabel(f"<b>{self.lang['license']}:</b>")
        license_section.setStyleSheet("font-size: 13px; padding: 10px 10px 5px 10px;")
        scroll_layout.addWidget(license_section)
        
        license_label = QLabel(self.lang['license_text'])
        license_label.setWordWrap(True)
        license_label.setStyleSheet("font-size: 11px; color: #444; padding: 0px 20px 10px 20px;")
        scroll_layout.addWidget(license_label)
        
        # Add stretch to push everything to the top
        scroll_layout.addStretch()
        
        main_layout.addWidget(scroll)
        
        # Close button
        btn_layout = QHBoxLayout()
        btn_close = QPushButton(self.lang['close'])
        btn_close.clicked.connect(self.accept)
        btn_close.setFixedWidth(100)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)
# -------------------------------
# Main Application
# -------------------------------
class PrayerTrayApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        
        self.config = Config()
        self.city = self.config.get('city', 'Tunis')
        self.method = self.config.get('method', 'Karachi')
        
        # Initialize PrayTimes with method and time format
        self.pt = PrayTimes(self.method)
        self.pt.timeFormat = self.config.get('time_format', '24h')
        
        # Add this line
        self.active_add_city_dialog = None
        
        # Ensure offsets are properly initialized
        for name in self.pt.timeNames:
            self.pt.offset[name] = 0
        
        self.last_notified_prayer = None
        self.notification_widget = None
        self.desktop_widget = None
        self.hover_dialog = None
        self.times = {}
        
        # Tray setup - ONLY CREATE ONCE
        self.tray = QSystemTrayIcon()
        pix = QPixmap(32, 32)
        pix.fill(QColor(0, 200, 0))
        self.tray.setIcon(QIcon(pix))
        
        # Connect the activated signal BEFORE showing the tray
        self.tray.activated.connect(self.on_tray_activated)
        
        # Create the context menu BEFORE showing the tray
        self.update_menu()
        
        # Now show the tray icon
        self.tray.setVisible(True)
        
        # Timer for refresh
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        
        self.update_times()
    

    def on_tray_activated(self, reason):
        """Handle tray icon clicks"""
        if reason == QSystemTrayIcon.Trigger:  # Left click
            if self.hover_dialog and self.hover_dialog.isVisible():
                # If dialog is visible, close it
                self.hover_dialog.close()
                self.hover_dialog = None
            else:
                # If dialog is not visible, show it
                self.hover_dialog = HoverDialog(self)
                self.hover_dialog.update_content()
                self.hover_dialog.show_near_tray()
            
    
    def get_lang(self):
        return TRANSLATIONS[self.config.get('language', 'en')]
    

    def update_menu(self):
        try:
            lang = self.get_lang()
            menu = QMenu()
            
            # City submenu
            city_menu = QMenu(lang['city'], menu)
            cities = self.config.get('cities', {})
            
            # Sort cities but put Mecca first
            city_names = sorted(cities.keys())
            if "Mecca" in city_names:
                city_names.remove("Mecca")
                city_names.insert(0, "Mecca")
            
            for city_name in city_names:
                city_data = cities[city_name]
                
                # Get translated city name
                if 'names' in city_data:
                    current_lang = self.config.get('language', 'en')
                    translated_city = city_data['names'].get(current_lang, city_name)
                else:
                    translated_city = lang.get('cities', {}).get(city_name, city_name)
                
                # Construct display name (include Kaba emoji if Mecca)
                display_name = translated_city
                if city_name == "Mecca":
                    display_name = f"🕋 {translated_city}"

                action = QAction(display_name, city_menu)
                action.setCheckable(True) # Make the action checkable
                action.setChecked(city_name == self.city) # Set checked if it's the current city
                action.triggered.connect(lambda checked, c=city_name: self.change_city(c))
                city_menu.addAction(action)
            
            city_menu.addSeparator()
            add_city_action = QAction(lang['add_city'], city_menu)
            add_city_action.triggered.connect(self.add_city)
            city_menu.addAction(add_city_action)
            
            edit_city_action = QAction(lang['edit_city'], city_menu)
            edit_city_action.triggered.connect(self.edit_city)
            city_menu.addAction(edit_city_action)
            
            # Delete submenu - exclude Mecca
            delete_menu = QMenu(lang['delete_city'], city_menu)
            for city_name in city_names:
                if city_name == "Mecca":
                    continue  # Skip Mecca in the delete menu
                
                city_data = cities[city_name]
                if 'names' in city_data:
                    current_lang = self.config.get('language', 'en')
                    translated_city = city_data['names'].get(current_lang, city_name)
                else:
                    translated_city = lang.get('cities', {}).get(city_name, city_name)
                
                action = QAction(translated_city, delete_menu)
                action.triggered.connect(lambda checked, c=city_name: self.delete_specific_city(c))
                delete_menu.addAction(action)
            
            city_menu.addMenu(delete_menu)
            menu.addMenu(city_menu)
            
            # Method submenu
            method_menu = QMenu(lang['method'], menu)
            for method_key in PrayTimes.methods.keys():
                translated_method = lang.get('methods', {}).get(method_key, method_key)
                display_name = translated_method # No manual checkmark or padding

                action = QAction(display_name, method_menu)
                action.setCheckable(True) # Make the action checkable
                action.setChecked(method_key == self.method) # Set checked if it's the current method
                action.triggered.connect(lambda checked, m=method_key: self.change_method(m))
                method_menu.addAction(action)
            menu.addMenu(method_menu)
            
            menu.addSeparator()
            
            # Settings
            settings_action = QAction(lang['settings'], menu)
            settings_action.triggered.connect(self.show_settings)
            menu.addAction(settings_action)
            
            # Desktop Widget
            widget_action = QAction(lang['desktop_widget'], menu)
            widget_action.triggered.connect(self.toggle_desktop_widget)
            menu.addAction(widget_action)

            # About
            about_action = QAction(lang['about'], menu)
            about_action.triggered.connect(self.show_about)
            menu.addAction(about_action)

            menu.addSeparator()                        
            
            # Quit
            quit_action = QAction(lang['quit'], menu)
            quit_action.triggered.connect(self.app.quit)
            menu.addAction(quit_action)
            
            self.tray.setContextMenu(menu)
        except Exception as e:
            print(f"Error updating menu: {e}")
            import traceback
            traceback.print_exc()
    def show_about(self):
        """Show the About dialog"""
        dialog = AboutDialog(self.config.get('language', 'en'))
        dialog.exec_()
    
    def add_city(self):
        lang = self.get_lang()
        dialog = AddCityDialog(
            self.config.get('cities', {}),
            self.config.get('language', 'en'),
            edit_mode=False
        )
                    
            
        self.active_add_city_dialog = dialog
        
        if dialog.exec_() == QDialog.Accepted and dialog.result_data:
            cities = self.config.get('cities', {})
            city_key = dialog.result_data['name']
            
            cities[city_key] = {
                'coords': dialog.result_data['coords'],
                'timezone': dialog.result_data.get('timezone', 0),
                'dst': dialog.result_data.get('dst', 0),
                'source': dialog.result_data['source'],
                'names': dialog.result_data['names']
            }
            
            self.config.set('cities', cities)
            QTimer.singleShot(100, self.update_menu)
        
        self.active_add_city_dialog = None
        
    # def add_city(self):
        # dialog = AddCityDialog(self.config.get('cities', {}), self.config.get('language', 'en'))
        
        # # Store reference to dialog to update its language if settings change
        # self.active_add_city_dialog = dialog
        
        # if dialog.exec_() == QDialog.Accepted and dialog.result_data:
            # cities = self.config.get('cities', {})
            # cities[dialog.result_data['name']] = {
                # 'coords': dialog.result_data['coords'],
                # 'timezone': dialog.result_data.get('timezone', 0),
                # 'dst': dialog.result_data.get('dst', 0),
                # 'source': dialog.result_data['source']
            # }
            # self.config.set('cities', cities)
            # # Defer menu update to avoid crash
            # QTimer.singleShot(100, self.update_menu)
        
        # # Clear reference
        # self.active_add_city_dialog = None
    
        
    
    def edit_city(self):
        lang = self.get_lang()
        
        # Get the currently selected city
        if not self.city:
            QMessageBox.information(None, lang['edit_city'], lang['select_city_to_edit'])
            return
        
        dialog = AddCityDialog(
            self.config.get('cities', {}),
            self.config.get('language', 'en'),
            edit_mode=True,
            city_to_edit=self.city
        )
        
        self.active_add_city_dialog = dialog
        
        if dialog.exec_() == QDialog.Accepted and dialog.result_data:
            cities = self.config.get('cities', {})
            
            # If the city name changed, remove old entry
            if dialog.result_data['is_edit'] and dialog.result_data['original_name'] != dialog.result_data['name']:
                old_name = dialog.result_data['original_name']
                if old_name in cities:
                    del cities[old_name]
                
                # Update current city if it was the edited one
                if self.city == old_name:
                    self.city = dialog.result_data['name']
                    self.config.set('city', self.city)
            
            # Add/update the city
            city_key = dialog.result_data['name']
            cities[city_key] = {
                'coords': dialog.result_data['coords'],
                'timezone': dialog.result_data.get('timezone', 0),
                'dst': dialog.result_data.get('dst', 0),
                'source': dialog.result_data['source'],
                'names': dialog.result_data['names']
            }
            
            self.config.set('cities', cities)
            self.update_times()
            QTimer.singleShot(100, self.update_menu)
        
        self.active_add_city_dialog = None

    
    def delete_specific_city(self, city_name):
        """Delete a specific city by name"""
        lang = self.get_lang()
        cities = self.config.get('cities', {})
        
        # Protect Mecca - it cannot be deleted
        if city_name == "Mecca":
            QMessageBox.warning(None, lang['delete_city'], lang['cannot_delete_mecca'])
            return
        
        # Check if there's more than one city (besides Mecca, there should be at least one other)
        if len(cities) <= 1:
            QMessageBox.warning(None, lang['delete_city'], "Cannot delete the last remaining city.")
            return
        
        # Get translated city name for confirmation
        city_data = cities.get(city_name, {})
        if 'names' in city_data:
            current_lang = self.config.get('language', 'en')
            translated_city = city_data['names'].get(current_lang, city_name)
        else:
            translated_city = lang.get('cities', {}).get(city_name, city_name)
        
        reply = QMessageBox.question(
            None,
            lang['confirm_delete'],
            f"{lang['confirm_delete_msg']}\n\n{translated_city}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            # Delete the city
            if city_name in cities:
                del cities[city_name]
                self.config.set('cities', cities)
                
                # If we deleted the currently active city, switch to Mecca
                if city_name == self.city:
                    self.city = "Mecca"
                    self.config.set('city', "Mecca")
                    self.update_times()
                
                QTimer.singleShot(100, self.update_menu)
            
            
                
    def change_city(self, city):
        self.city = city
        self.config.set('city', city)
        self.update_times()
        # Defer menu update to avoid crash
        QTimer.singleShot(100, self.update_menu)


    def change_method(self, method):
        try:
            #print(f"=== Changing method from {self.method} to {method} ===")
            self.method = method
            self.config.set('method', method)
            
            # Use setMethod instead of recreating the object
            #print(f"Setting method to: {method}")
            self.pt.setMethod(method)
            #print(f"Method set successfully")
            
            # Update times with new method
            #print("Calling update_times()...")
            self.update_times()
            #print("update_times() completed")
            
            # Defer menu update to avoid crash
            #print("Scheduling menu update...")
            QTimer.singleShot(100, self.update_menu)
            #print("=== Method change completed ===")
        except Exception as e:
            print(f"!!! ERROR in change_method: {e}")
            import traceback
            traceback.print_exc()
        
        
   
    
            
    def show_settings(self):
        old_lang = self.config.get('language', 'en')
        old_format = self.config.get('time_format', '24h')
        
        dialog = SettingsDialog(self.config)
        if dialog.exec_() == QDialog.Accepted:
            new_lang = self.config.get('language', 'en')
            new_format = self.config.get('time_format', '24h')
            
            # Update PrayTimes time format
            self.pt.timeFormat = new_format
            
            # If language changed, need to update everything
            if old_lang != new_lang:
                # Update active AddCityDialog if it exists
                if hasattr(self, 'active_add_city_dialog') and self.active_add_city_dialog:
                    self.active_add_city_dialog.update_language(new_lang)
                
                # Close and recreate desktop widget if it exists
                if self.desktop_widget:
                    self.desktop_widget.close()
                    self.desktop_widget = None
                    # Recreate with new language
                    QTimer.singleShot(200, self.recreate_desktop_widget)
                
                # Close hover dialog if open
                if self.hover_dialog:
                    self.hover_dialog.close()
                    self.hover_dialog = None
                    # REMOVE this line: self.was_hovering = False
            
            # If time format changed, update times display
            if old_format != new_format or old_lang != new_lang:
                self.update_times()
            
            # Defer menu update to avoid crash
            QTimer.singleShot(100, self.update_menu)
        
        

    def recreate_desktop_widget(self):
        """Helper method to recreate desktop widget after language change"""
        if self.desktop_widget is None:
            self.desktop_widget = DesktopWidget(self.config, self)
            self.desktop_widget.show()
    
    def toggle_desktop_widget(self):
        if self.desktop_widget is None:
            self.desktop_widget = DesktopWidget(self.config, self)
            self.desktop_widget.show()
        else:
            self.desktop_widget.close()
            self.desktop_widget = None

    def update_times(self):
        try:
            cities = self.config.get('cities', {})
            if self.city not in cities:
                return
            
            city_data = cities[self.city]
            coords = city_data['coords']
            
            # Use stored timezone or default to 1 for Tunis
            if 'timezone' in city_data:
                city_timezone = city_data['timezone']
            else:
                city_timezone = 1
            
            # DST flag (0 = no DST, 1 = DST active)
            dst = city_data.get('dst', 0)
            
            today = date.today()
            
            # Get the configured time format
            time_format = self.config.get('time_format', '24h')
            
            # Always get times in 24h format for internal calculations
            # We'll format them for display later
            self.pt.timeFormat = '24h'
            
            # Call getTimes
            times_dict = self.pt.getTimes(today, coords, city_timezone, dst)
            
            # Convert string times to datetime objects
            self.times = {}
            for prayer in ['fajr', 'sunrise', 'dhuhr', 'asr', 'maghrib',  'isha']:
                if prayer in times_dict:
                    time_str = times_dict[prayer]
                    if time_str and time_str != '-----':
                        try:
                            # Parse HH:MM format (24-hour)
                            parts = time_str.split(':')
                            hour = int(parts[0])
                            minute = int(parts[1])
                            # Create datetime object
                            prayer_time = datetime.combine(today, datetime.min.time()) + timedelta(hours=hour, minutes=minute)
                            self.times[prayer] = prayer_time
                        except Exception as e:
                            print(f"Error parsing {prayer}: {time_str}, {e}")
            
            # Add sunset
            if 'sunset' in times_dict:
                time_str = times_dict['sunset']
                if time_str and time_str != '-----':
                    try:
                        parts = time_str.split(':')
                        hour = int(parts[0])
                        minute = int(parts[1])
                        self.times['sunset'] = datetime.combine(today, datetime.min.time()) + timedelta(hours=hour, minutes=minute)
                    except:
                        self.times['sunset'] = self.times.get('maghrib', datetime.now())
            else:
                self.times['sunset'] = self.times.get('maghrib', datetime.now())
            
            self.refresh_tray()
        except Exception as e:
            print(f"Error in update_times: {e}")
            import traceback
            traceback.print_exc()
            
    def format_time(self, dt):
        """Format datetime according to user's preference"""
        time_format = self.config.get('time_format', '24h')
        if time_format == '12h':
            return dt.strftime('%I:%M %p').lstrip('0')  # Remove leading zero from hour
        else:
            return dt.strftime('%H:%M')        
     
    def refresh_tray(self):
        if not self.times:
            return
        
        now = datetime.now()
        future = [(p, t) for p, t in self.times.items() if t > now]
        if not future:
            next_prayer, next_time = list(self.times.items())[0]
            next_time += timedelta(days=1)
        else:
            next_prayer, next_time = future[0]
        
        remaining = next_time - now
        h, m = divmod(int(remaining.total_seconds() // 60), 60)
        remaining_str = f"{h}h {m}m"
        
        # Create the icon with reservoir animation
        icon_size = 32
        pix = QPixmap(icon_size, icon_size)
        pix.fill(Qt.transparent)
        
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing, True)
        
        total_sec = remaining.total_seconds()
        countdown_period = 5 * 60
        
        if total_sec <= countdown_period:
            fill_percent = 1.0 - (total_sec / countdown_period)
            red_height = int(icon_size * fill_percent)
            
            painter.setBrush(QColor(0, 200, 0))
            painter.setPen(Qt.NoPen)
            painter.drawRect(0, red_height, icon_size, icon_size - red_height)
            
            painter.setBrush(QColor(255, 0, 0))
            painter.setPen(Qt.NoPen)
            painter.drawRect(0, 0, icon_size, red_height)
            
            if icon_size >= 24:
                minutes_left = int(total_sec // 60)
                painter.setPen(Qt.white)
                painter.setFont(QFont('Arial', 10, QFont.Bold))
                painter.drawText(0, 0, icon_size, icon_size, Qt.AlignCenter, str(minutes_left))
        else:
            painter.setBrush(QColor(0, 200, 0))
            painter.setPen(Qt.NoPen)
            painter.drawRect(0, 0, icon_size, icon_size)
        
        painter.end()
        
        self.tray.setIcon(QIcon(pix))
        
        lang = self.get_lang()
        current_lang = self.config.get('language', 'en')

        prayer_name = lang['prayers'].get(next_prayer, next_prayer)
        RLM = "\u200F"  # Right-to-Left Mark
        LRM = "\u200E"  # Left-to-Right Mark

        # Get translated city name
        cities = self.config.get('cities', {})
        city_data = cities.get(self.city, {})

        if 'names' in city_data:
            # Get translated name for current language
            translated_city = city_data['names'].get(current_lang, self.city)
        else:
            # Fallback to translations dictionary or original city name
            translated_city = lang.get('cities', {}).get(self.city, self.city)

        if current_lang == 'ar':
            # Arabic: Use RTL formatting
            # Format: City | Time - Prayer : Label
            tooltip = f"{LRM}{remaining_str}- {RLM}{prayer_name}{LRM} : {RLM}{lang['next_prayer']}{LRM} | {RLM}{translated_city}"
        else:
            # English/French: Use LTR formatting
            # Format: City | Label: Prayer - Time
            tooltip = f"{translated_city} | {lang['next_prayer']}: {prayer_name} - {remaining_str}"

        self.tray.setToolTip(tooltip)


        #self.tray.setToolTip(f"{self.city} | {lang['next_prayer']}: {prayer_name} - {remaining_str}")
    
    
        
    
    def refresh(self):
        self.update_times()
        
        # Check if prayer time reached
        now = datetime.now()
        for prayer, prayer_time in self.times.items():
            if abs((prayer_time - now).total_seconds()) < 1:
                if self.last_notified_prayer != prayer:
                    self.show_prayer_notification(prayer)
                    self.last_notified_prayer = prayer
        
        # Update desktop widget if visible
        if self.desktop_widget and self.desktop_widget.isVisible():
            self.desktop_widget.update()
    
    def show_prayer_notification(self, prayer):
        if not self.config.get('show_notification', True):
            return
        
        duration = self.config.get('notification_duration', 10)
        if duration == 0:
            return
        
        self.notification_widget = PrayerNotification(self.city, prayer, self.config.get('language', 'en'))
        self.notification_widget.show()
        
        # Auto-close after duration
        QTimer.singleShot(duration * 1000, lambda: self.notification_widget.close() if self.notification_widget else None)
    
    def run(self):
        sys.exit(self.app.exec_())

# -------------------------------
# Run the App
# -------------------------------
if __name__ == "__main__":
    app = PrayerTrayApp()        
    app.run()
