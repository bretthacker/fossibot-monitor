# fossibot/registers.py
# Register map reverse-engineered from the Fossibot+ app (uni.app.UNI620F75F)
# Each entry: key -> (name, data_type, unit, description)
#
# data_type values:
#   uint8_t  - 1 byte unsigned
#   bool     - 1 byte, 0 or 1
#   uint16_t - 2 bytes unsigned little-endian
#   int16_t  - 2 bytes signed little-endian
#   uint32_t - 4 bytes unsigned little-endian
#   int32_t  - 4 bytes signed little-endian
#   version  - 4 bytes, displayed as hex date bytes reversed (e.g. BMS version)

REGISTERS = {
    1:  ("soc",                     "uint8_t",  "%",    "State of charge 0-100"),
    2:  ("battery_temp_max",        "int32_t",  "°C",   "Battery max temperature"),
    3:  ("runtime_minutes",         "uint16_t", "min",  "Estimated runtime remaining"),
    4:  ("charge_discharge_state",  "uint8_t",  "",     "0=idle, 1=charging, 0=discharging"),
    5:  ("bms_version",             "version",  "",     "BMS firmware version (hex date)"),
    6:  ("battery_temp_min",        "int16_t",  "°C",   "Battery min temperature"),
    7:  ("bms_mos_temp",            "int16_t",  "°C",   "BMS MOS temperature"),
    8:  ("pack_voltage",            "uint16_t", "0.1V", "Total pack voltage"),
    9:  ("current_ma",              "int32_t",  "mA",   "Current (negative=discharge)"),
    10: ("bus_voltage",             "uint16_t", "0.1V", "BUS voltage"),
    11: ("balance_state",           "uint32_t", "",     "Cell balance state bitmap"),
    12: ("bms_fault_code",          "uint32_t", "",     "BMS fault code"),
    18: ("pcs_version",             "uint16_t", "",     "PCS/MPPT firmware version (hex)"),
    19: ("ac_input_power",          "uint16_t", "W",    "AC input (charging) power"),
    20: ("ac_output_power",         "uint16_t", "W",    "AC output (discharging) power"),
    21: ("output_voltage",          "uint16_t", "0.1V", "AC output voltage"),
    22: ("output_frequency",        "uint16_t", "Hz",   "AC output frequency"),
    23: ("pv_charge_power",         "uint16_t", "W",    "Solar PV charging power"),
    24: ("inverter_temp",           "int16_t",  "°C",   "Inverter temperature"),
    25: ("mos_temp",                "int16_t",  "°C",   "MOS temperature"),
    26: ("pcs_fault_code",          "uint32_t", "",     "PCS fault code"),
    27: ("pv_input_voltage",        "uint16_t", "0.1V", "Solar PV input voltage"),
    28: ("pv_input_current_ma",     "int32_t",  "mA",   "Solar PV input current"),
    29: ("pv_run_mode",             "uint8_t",  "",     "PV operating mode"),
    30: ("pv_fault_code",           "uint32_t", "",     "PV fault code"),
    34: ("total_input_power",       "uint16_t", "W",    "Total input power (AC+PV)"),
    35: ("total_output_power",      "uint16_t", "W",    "Total output power (AC+DC+USB)"),
    36: ("dc_power",                "uint16_t", "W",    "DC output power"),
    37: ("usb_power",               "uint16_t", "0.1W", "USB output power"),
    38: ("led_state",               "uint8_t",  "",     "LED: 0=off 1=on 2=SOS 3=strobe"),
    39: ("ac_switch",               "bool",     "",     "AC output switch 0=off 1=on"),
    40: ("dc_switch",               "bool",     "",     "DC output switch 0=off 1=on"),
    41: ("usb_switch",              "bool",     "",     "USB output switch 0=off 1=on"),
    42: ("charge_power_setting",    "uint16_t", "W",    "Charge power setting (100-1200W)"),
    43: ("output_memory",           "uint8_t",  "",     "Output memory on power restore"),
    44: ("screen_off_time",         "uint8_t",  "",     "Screen off: 0=never 1=30s 2=1m 3=5m 4=10m 5=30m"),
    45: ("shutdown_time",           "uint8_t",  "",     "Shutdown: 0=never 1=5m 2=10m 3=1hr 4=8hr"),
    46: ("eco_ups_mode",            "bool",     "",     "Mode: 0=UPS 1=ECO"),
    47: ("dc_version",              "version",  "",     "DC firmware version (hex date)"),
    48: ("pv_total_kwh",            "uint16_t", "kWh",  "PV total charging energy"),
    49: ("charge_upper_limit",      "uint8_t",  "%",    "Charge upper limit 60-100%"),
    50: ("discharge_lower_limit",   "uint8_t",  "%",    "Discharge lower limit 0-20%"),
    51: ("button_sound",            "uint8_t",  "",     "Button sound: 0=on 1=off"),
    52: ("screen_brightness",       "uint8_t",  "%",    "Screen brightness 0-100%"),
    53: ("dc_charge_current",       "uint8_t",  "A",    "DC charging current 1-8A (or 15A expanded)"),
    54: ("scheduled_charge",        "uint8_t",  "",     "Scheduled charge: 0=off 1=on"),
    55: ("scheduled_discharge",     "uint8_t",  "",     "Scheduled discharge: 0=off 1=on"),
    56: ("dc_standby_time",         "uint8_t",  "",     "DC no-load standby: 0=never 1=30m 2=1hr 3=4hr 4=8hr 5=12hr 6=24hr"),
    57: ("usb_standby_time",        "uint8_t",  "",     "USB no-load standby: same enum as dc_standby_time"),
    58: ("ac_standby_time",         "uint8_t",  "",     "AC no-load standby: same enum as dc_standby_time"),
}

# Human-readable name lookup by key
def register_name(key):
    r = REGISTERS.get(key)
    return r[0] if r else f"reg_{key}"

def register_unit(key):
    r = REGISTERS.get(key)
    return r[2] if r else ""
