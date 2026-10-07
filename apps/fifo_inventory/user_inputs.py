# user_inputs.py

ENGINE_VERSION = "2.2.0"
SNAPSHOT_SCHEMA_VERSION = 2
OPENING_SEED_FISCAL_YEAR = 2026
QTY_TOLERANCE = 0.0001
VALUE_TOLERANCE = 0.01
ZERO_COST_TOLERANCE = 0.0

PRODUCTS = {
    2: 'W1228 - PREFORMS / PLASTIPAK 8.5', 3: 'W0082 - BLUE CAPS', 4: 'W0082 - NATURAL CAPS',
    5: 'PP LABELS', 6: 'LOWES LABELS', 7: 'FOOD KING LABELS', 8: 'FOOD CLUB LABELS',
    9: 'JUNIORS LABELS', 10: 'PLAINS LABELS', 11: 'TNT LABELS', 12: 'ALLSUPS LABELS',
    13: 'SPRINGHOUSE LABELS', 14: 'PP24 FILM', 15: 'PP32 FILM', 16: 'PP40 FILM',
    17: 'LOW24 FILM', 18: 'LOW32 FILM', 19: 'LOW40 FILM', 20: 'FDK24 FILM',
    21: 'FDK40 FILM', 22: 'FC24 FILM', 23: 'FC32 FILM', 24: 'FC40 FILM',
    25: 'JUNIORS 24 FILM', 26: 'PLAINS 24 FILM', 27: 'SPRINGHOUSE 24 FILM',
    28: 'TNT 24 FILM', 29: 'ALLSUPS 24 FILM', 30: 'GLUE KRONES'
}

PERIOD_12_OPENING_LAYERS = {
    #W1228 - PREFORMS / PLASTIPAK 8.5 period 12-2026 opening layers
    2: [
        {'date': 'PD11-26: 07/03/26-07/30/26', 'qty': 10076832, 'unit_cost': 0.025439997687545, 'total_value': 256354.58},
    ],
    # W0082 - BLUE CAPS period 12-2026 opening layers
    3: [
        {'date': '2026-10-14', 'qty': 3400000, 'unit_cost': 0.0075185556, 'total_value': 25563.09}
    ],
    # W0082 - NATURAL CAPS period 12-2026 opening layers
    4: [
        {'date': '2026-07-21', 'qty': 1900000, 'unit_cost': 0.006870909, 'total_value': 13054.73},
        {'date': '2026-07-27', 'qty': 9000000, 'unit_cost': 0.00713, 'total_value': 64170.00},
    ],
    # PP LABELS period 12-2026 opening layers
    5: [
        {'date': '2026-07-17 (L6-112601)', 'qty': 3806669, 'unit_cost': 0.003210741, 'total_value': 12222.23},
        {'date': '2026-07-22 (L6-112602)', 'qty': 14616300, 'unit_cost': 0.002470025, 'total_value': 36102.63},
    ],
    # LOWES LABELS period 12-2026 opening layers
    6: [
        {'date': '2026-04-30', 'qty': 1925464, 'unit_cost': 0.002728, 'total_value': 5252.69},
        {'date': '2026-05-13', 'qty': 7635600, 'unit_cost': 0.002440, 'total_value': 18630.86},
        {'date': '2026-06-01', 'qty': 315000, 'unit_cost': 0.003310, 'total_value': 1042.51},
        {'date': '2026-06-02', 'qty': 3161700, 'unit_cost': 0.002909, 'total_value': 9196.28},
        {'date': '2026-06-29', 'qty': 15987000, 'unit_cost': 0.002450, 'total_value': 39168.15},
    ],
    # FOOD KING LABELS period 12-2026 opening layers
    7: [
        {'date': '2026-04-30', 'qty': 1898291, 'unit_cost': 0.002609999, 'total_value': 4954.54},
        {'date': '2026-05-13', 'qty': 7746800, 'unit_cost': 0.00244, 'total_value': 18902.19},
        {'date': '2026-06-01', 'qty': 315000, 'unit_cost': 0.0024406349, 'total_value': 768.80},
        {'date': '2026-06-02', 'qty': 3139200, 'unit_cost': 0.0024400006, 'total_value': 7659.65},
        {'date': '2026-07-24', 'qty': 10491900, 'unit_cost': 0.00255, 'total_value': 26754.35},
    ],
    # FOOD CLUB LABELS period 12-2026 opening layers
    8: [
        {'date': '2026-05-11', 'qty': 2715063, 'unit_cost': 0.00261, 'total_value': 7086.31},
        {'date': '2026-06-16', 'qty': 10800000, 'unit_cost': 0.00245, 'total_value': 26460.0},
        {'date': '2026-06-26', 'qty': 2115000, 'unit_cost': 0.002706099, 'total_value': 5723.40},
        {'date': '2026-07-22', 'qty': 8873100, 'unit_cost': 0.00247, 'total_value': 21916.56},
    ],
    # JUNIORS LABELS period 12-2026 opening layers
    9: [
        {'date': '2025-07-01', 'qty': 1385535, 'unit_cost': 0.00511415533980583, 'total_value': 7085.84},
    ],
    # PLAINS LABELS period 12-2026 opening layers
    10: [
        {'date': 'Unknown - Legacy Layer', 'qty': 12341201, 'unit_cost': 0.00221, 'total_value': 27274.05},
    ],
    # TNT LABELS period 12-2026 opening layers
    11: [
        {'date': 'PD5-26: 2026-02-06', 'qty': 2994781, 'unit_cost': 0.00241002, 'total_value': 7217.47},
    ],
    # ALLSUPS LABELS period 12-2026 opening layers
    12: [
        {'date': 'P10-26: 2026-06-16', 'qty': 2616302, 'unit_cost': 0.0027, 'total_value': 7064.02},
        {'date': 'P10-26: 2026-06-22 to 2026-06-29', 'qty': 22485300, 'unit_cost': 0.00245, 'total_value': 55088.99},
        {'date': 'P11-26: 2026-07-04', 'qty': 378400, 'unit_cost': 0.0031698203, 'total_value': 1199.46},
    ],
    # SPRINGHOUSE LABELS period 12-2026 opening layers
    13: [
        {'date': '2025-07-16', 'qty': 1910671, 'unit_cost': 0.004247278, 'total_value': 8115.15},
    ],
    # PP24 FILM period 12-2026 opening layers
    14: [
        {'date': '2026-05-04', 'qty': 446154, 'unit_cost': 0.1821, 'total_value': 81244.64},
        {'date': '2026-06-22', 'qty': 502939, 'unit_cost': 0.20999999801, 'total_value': 105617.19},
    ],
    # PP32 FILM period 12-2026 opening layers
    15: [
        {'date': '2022 (Legacy)', 'qty': 142113, 'unit_cost': 0.2047, 'total_value': 29090.53},
        {'date': '2022 (Legacy)', 'qty': 194750, 'unit_cost': 0.2129, 'total_value': 41462.28},
    ],
    # PP40 FILM period 12-2026 opening layers
    16: [
        {'date': '2026-05-18', 'qty': 153034, 'unit_cost': 0.2550699859, 'total_value': 39034.38},
        {'date': '2026-06-22', 'qty': 202719, 'unit_cost': 0.3033200144, 'total_value': 61488.73},
        {'date': '2026-06-23', 'qty': 312309, 'unit_cost': 0.3033200132, 'total_value': 94729.57},
    ],
    # LOW24 FILM period 12-2026 opening layers
    17: [
        {'date': '2025-06-27', 'qty': 332039, 'unit_cost': 0.1798, 'total_value': 59690.65},
        {'date': '2025-10-23', 'qty': 73659, 'unit_cost': 0.1818, 'total_value': 13390.47},
        {'date': '2026-04-29', 'qty': 500515, 'unit_cost': 0.1821, 'total_value': 91143.78},
        {'date': '2026-06-23', 'qty': 12031, 'unit_cost': 0.1821, 'total_value': 2190.85},
    ],
    # LOW32 FILM period 12-2026 opening layers
    18: [],
    # LOW40 FILM period 12-2026 opening layers
    19: [
        {'date': '2025-06-27', 'qty': 228507, 'unit_cost': 0.2519, 'total_value': 57565.48},
        {'date': '2025-07-01', 'qty': 35149, 'unit_cost': 0.2519, 'total_value': 8854.74},
        {'date': '2025-08-11 to 2025-08-12', 'qty': 550486, 'unit_cost': 0.2436, 'total_value': 134120.41},
    ],
    # FDK24 FILM period 12-2026 opening layers
    20: [
        {'date': 'Unknown - Legacy Layer', 'qty': 403, 'unit_cost': 0.1683, 'total_value': 67.82},
        {'date': 'Unknown - Legacy Layer', 'qty': 7068, 'unit_cost': 0.1969, 'total_value': 1391.69},
        {'date': '2025-07-01', 'qty': 179668, 'unit_cost': 0.1723, 'total_value': 30958.59},
        {'date': '2026-01-21', 'qty': 49731, 'unit_cost': 0.1742, 'total_value': 8661.65},
        {'date': '2026-05-18', 'qty': 94520, 'unit_cost': 0.195, 'total_value': 18430.45},
    ],
    # FDK40 FILM period 12-2026 opening layers
    21: [
        {'date': '2026-01-21', 'qty': 254940, 'unit_cost': 0.25464, 'total_value': 64917.92},
    ],
    # FC24 FILM period 12-2026 opening layers
    22: [
        {'date': '2026-04-29', 'qty': 112750, 'unit_cost': 0.1811, 'total_value': 20417.89},
        {'date': '2026-06-29', 'qty': 496665, 'unit_cost': 0.20204007, 'total_value': 100346.23},
    ],
    # FC32 FILM period 12-2026 opening layers
    23: [
        {'date': 'Unknown - Legacy Layer', 'qty': 90576, 'unit_cost': 0.1549, 'total_value': 14031.85},
        {'date': 'Unknown - Legacy Layer', 'qty': 53368, 'unit_cost': 0.2339, 'total_value': 12482.78},
    ],
    # FC40 FILM period 12-2026 opening layers
    24: [
        {'date': 'Unknown - Legacy Layer', 'qty': 7221, 'unit_cost': 0.2326, 'total_value': 1679.41},
        {'date': 'Unknown - Legacy Layer', 'qty': 12412, 'unit_cost': 0.1969, 'total_value': 2443.30},
        {'date': 'Unknown - Legacy Layer', 'qty': 210, 'unit_cost': 0.2411, 'total_value': 50.64},
        {'date': 'PD10.25: 2025-06-16', 'qty': 221757, 'unit_cost': 0.24051011, 'total_value': 53334.78},
    ],
    # JUNIORS 24 FILM period 12-2026 opening layers
    25: [
        {'date': '2025-07-11', 'qty': 192692, 'unit_cost': 0.1719, 'total_value': 33119.90},
        {'date': '2025-08-12', 'qty': 264071, 'unit_cost': 0.1719, 'total_value': 45388.52},
        {'date': '2025-10-22', 'qty': 8161, 'unit_cost': 0.175, 'total_value': 1428.01},
    ],
    # PLAINS 24 FILM period 12-2026 opening layers
    26: [
        {'date': 'Unknown - Legacy Layer', 'qty': 64036, 'unit_cost': 0.1995, 'total_value': 12776.06},
    ],
    # SPRINGHOUSE 24 FILM period 12-2026 opening layers
    27: [
        {'date': 'Unknown - Legacy Layer', 'qty': 17601, 'unit_cost': 0.19, 'total_value': 3343.49},
        {'date': '2026-04-29', 'qty': 96042, 'unit_cost': 0.20745997, 'total_value': 19924.87},
    ],
    # TNT 24 FILM period 12-2026 opening layers
    28: [
        {'date': 'Unknown - Legacy Layer', 'qty': 111254, 'unit_cost': 0.1725, 'total_value': 19191.32},
        {'date': 'Unknown - Legacy Layer', 'qty': 285053, 'unit_cost': 0.210120013, 'total_value': 59895.34},
    ],
    # ALLSUPS 24 FILM period 12-2026 opening layers
    29: [
        {'date': '2025-12-22', 'qty': 76941, 'unit_cost': 0.1881, 'total_value': 14471.83},
        {'date': '2026-05-18', 'qty': 275529, 'unit_cost': 0.2006, 'total_value': 55271.12},
        {'date': '2026-05-19', 'qty': 7492, 'unit_cost': 0.25506674, 'total_value': 1910.98},
    ],
    # GLUE KRONES period 12-2026 opening layers
    30: [
        {'date': 'Unknown - Legacy Layer', 'qty': 3, 'unit_cost': 122.1268, 'total_value': 366.38},
        {'date': '2026-06-04', 'qty': 50, 'unit_cost': 124.548, 'total_value': 6227.40},
    ],
}