import math
from datetime import datetime, timedelta
from typing import Dict, List, Any
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import swisseph as swe

app = FastAPI(title="Parashara Vedic Astrology & 9-Year Prediction Engine")
# Set Lahiri Ayanamsa (Chitrapaksha)
swe.set_sid_mode(swe.SIDM_LAHIRI)

RASHIS = [
    "Aries", "Taurus", "Gemini", "Cancer",
    "Leo", "Virgo", "Libra", "Scorpio",
    "Sagittarius", "Capricorn", "Aquarius", "Pisces"
]

RASHI_LORDS = {
    0: "Mars", 1: "Venus", 2: "Mercury", 3: "Moon",
    4: "Sun", 5: "Mercury", 6: "Venus", 7: "Mars",
    8: "Jupiter", 9: "Saturn", 10: "Saturn", 11: "Jupiter"
}

NAKSHATRAS = [
    "Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra",
    "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva Phalguni", "Uttara Phalguni",
    "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha", "Jyeshtha",
    "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana", "Dhanishta", "Shatabhisha",
    "Purva Bhadrapada", "Uttara Bhadrapada", "Revati"
]

# Vimshottari sequence and planetary periods in years
DASHA_ORDER = [
    ("Ketu", 7), ("Venus", 20), ("Sun", 6), ("Moon", 10),
    ("Mars", 7), ("Rahu", 18), ("Jupiter", 16), ("Saturn", 19), ("Mercury", 17)
]
DASHA_YEARS = {lord: yrs for lord, yrs in DASHA_ORDER}
TOTAL_DASHA_CYCLE = 120.0

PLANET_MAP = {
    "Sun": swe.SUN,
    "Moon": swe.MOON,
    "Mars": swe.MARS,
    "Mercury": swe.MERCURY,
    "Jupiter": swe.JUPITER,
    "Venus": swe.VENUS,
    "Saturn": swe.SATURN,
    "Rahu": swe.MEAN_NODE
}

class HoroscopeRequest(BaseModel):
    name: str
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int = 0
    latitude: float
    longitude: float
    timezone_offset: float  # e.g., 5.5 for IST

def get_julian_day(year: int, month: int, day: int, hour: int, minute: int, second: int, tz_offset: float) -> float:
    utc_hours = (hour + (minute / 60.0) + (second / 3600.0)) - tz_offset
    return swe.julday(year, month, day, utc_hours)

def calculate_sidereal_positions(jd: float, lat: float, lon: float) -> Dict[str, Any]:
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    flags = swe.FLG_SIDEREAL | swe.FLG_SPEED

    # 1. Ascendant / Houses (Equal 30-degree houses from Ascendant)
    houses, ascmc = swe.houses_ex(jd, lat, lon, b'A', flags)
    asc_deg = ascmc[0]
    asc_sign = int(asc_deg // 30)

    results = {
        "ascendant": {
            "longitude": asc_deg,
            "sign_idx": asc_sign,
            "sign": RASHIS[asc_sign],
            "degree_in_sign": asc_deg % 30
        },
        "planets": {}
    }

    # 2. Planetary Placements
    for name, p_id in PLANET_MAP.items():
        res, ret_flags = swe.calc_ut(jd, p_id, flags)
        lon_deg = res[0]
        speed = res[3]
        sign_idx = int(lon_deg // 30)
        house_num = ((sign_idx - asc_sign) % 12) + 1

        results["planets"][name] = {
            "longitude": lon_deg,
            "sign_idx": sign_idx,
            "sign": RASHIS[sign_idx],
            "degree_in_sign": lon_deg % 30,
            "house": house_num,
            "is_retrograde": speed < 0
        }

    # Ketu is placed 180° opposite Rahu
    rahu_deg = results["planets"]["Rahu"]["longitude"]
    ketu_deg = (rahu_deg + 180.0) % 360.0
    ketu_sign = int(ketu_deg // 30)
    ketu_house = ((ketu_sign - asc_sign) % 12) + 1
    results["planets"]["Ketu"] = {
        "longitude": ketu_deg,
        "sign_idx": ketu_sign,
        "sign": RASHIS[ketu_sign],
        "degree_in_sign": ketu_deg % 30,
        "house": ketu_house,
        "is_retrograde": True
    }

    # 3. Nakshatra Details for Moon
    moon_lon = results["planets"]["Moon"]["longitude"]
    nak_idx = int(moon_lon // (360.0 / 27.0))
    nak_fraction = (moon_lon % (360.0 / 27.0)) / (360.0 / 27.0)
    results["moon_nakshatra"] = {
        "index": nak_idx,
        "name": NAKSHATRAS[nak_idx],
        "pada": int(nak_fraction * 4) + 1,
        "fraction_elapsed": nak_fraction
    }

    return results

def calculate_vimshottari_timeline(birth_dt: datetime, nak_idx: int, fraction_elapsed: float) -> List[Dict[str, Any]]:
    order_lords = [x[0] for x in DASHA_ORDER]
    start_lord_idx = nak_idx % 9
    first_lord, first_yrs = DASHA_ORDER[start_lord_idx]

    remaining_first_yrs = first_yrs * (1.0 - fraction_elapsed)
    timeline = []
    
    current_start = birth_dt
    first_end = current_start + timedelta(days=remaining_first_yrs * 365.2425)
    timeline.append({
        "mahadasha": first_lord,
        "start": current_start,
        "end": first_end,
        "duration_years": remaining_first_yrs
    })
    
    current_start = first_end
    for i in range(1, 9):
        lord, yrs = DASHA_ORDER[(start_lord_idx + i) % 9]
        end_dt = current_start + timedelta(days=yrs * 365.2425)
        timeline.append({
            "mahadasha": lord,
            "start": current_start,
            "end": end_dt,
            "duration_years": yrs
        })
        current_start = end_dt

    return timeline

def get_active_dashas(timeline: List[Dict[str, Any]], target_dt: datetime) -> Dict[str, str]:
    for md in timeline:
        if md["start"] <= target_dt < md["end"]:
            md_name = md["mahadasha"]
            md_dur = DASHA_YEARS[md_name]
            elapsed_days = (target_dt - md["start"]).days
            
            # Antardasha sub-cycle
            sub_start_idx = [x[0] for x in DASHA_ORDER].index(md_name)
            sub_accum_days = 0.0
            ad_name = md_name
            for j in range(9):
                sub_lord, sub_yrs = DASHA_ORDER[(sub_start_idx + j) % 9]
                ad_duration_days = (md_dur * (sub_yrs / 120.0)) * 365.2425
                if sub_accum_days <= elapsed_days < (sub_accum_days + ad_duration_days):
                    ad_name = sub_lord
                    break
                sub_accum_days += ad_duration_days
                
            return {"mahadasha": md_name, "antardasha": ad_name}
            
    return {"mahadasha": "Undefined", "antardasha": "Undefined"}

def generate_9_year_predictions(birth_dt: datetime, chart_data: Dict[str, Any], timeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = datetime.now()
    asc_sign_idx = chart_data["ascendant"]["sign_idx"]
    predictions = []

    interpretations = {
        "Jupiter": {
            "career": "Expansion of responsibilities, favorable outcomes with authorities, and opportunities for professional mentorship.",
            "finance": "Favorable liquid wealth accumulation and prudent investments in assets.",
            "general": "Enhanced wisdom, spiritual inclination, and family celebration."
        },
        "Saturn": {
            "career": "Demanding workloads requiring structural discipline, patience, and persistent effort.",
            "finance": "Consolidation phase. Long-term property or structural commitments preferred over speculation.",
            "general": "Cultivates endurance and pragmatism; focus on bone health and joint care."
        },
        "Mercury": {
            "career": "Strong for business negotiations, analytical tasks, software architecture, and public communications.",
            "finance": "Diversified gains through trading, contracts, and intellectual property.",
            "general": "Mental agility remains sharp; manage nervous exhaustion with meditation."
        },
        "Venus": {
            "career": "Collaborative achievements, creative projects, and public goodwill.",
            "finance": "Expenditure on comforts, luxury goods, and vehicle acquisitions balanced by stable income.",
            "general": "Harmony in domestic and marital relationships."
        },
        "Sun": {
            "career": "Elevation in leadership status, visibility, and direct executive authority.",
            "finance": "Government support, bonuses, and recognition of personal merit.",
            "general": "Boost in vitality and self-assurance; maintain healthy blood pressure and eye care."
        },
        "Moon": {
            "career": "Public-facing initiatives, frequent travel, and dynamic shifts in work roles.",
            "finance": "Fluctuating liquid cash flow; prioritize steady budget allocations.",
            "general": "Emotional sensitivity; cultivate peace of mind and hydration."
        },
        "Mars": {
            "career": "High energy for competitive objectives, project execution, and problem solving.",
            "finance": "Real estate transactions and bold enterprise investments.",
            "general": "Drive for physical fitness; guard against impulsiveness and heat-related ailments."
        },
        "Rahu": {
            "career": "Unconventional pivots, digital scaling, foreign alignments, and rapid expansion.",
            "finance": "Sudden windfalls possible; exercise vigilance against unverified schemes.",
            "general": "Restlessness and ambition; adhere to clarity in speech and agreements."
        },
        "Ketu": {
            "career": "Reflective period with a preference for technical specialization, backend architecture, or advisory roles.",
            "finance": "Detached expenditure; focus on clearing outstanding liabilities.",
            "general": "High intuitive insight, philosophical pursuits, and spiritual breakthroughs."
        }
    }

    for year_idx in range(9):
        target_year_start = now + timedelta(days=year_idx * 365.2425)
        target_year_end = target_year_start + timedelta(days=365.2425)
        dasha_info = get_active_dashas(timeline, target_year_start)
        
        md = dasha_info["mahadasha"]
        ad = dasha_info["antardasha"]
        
        # Calculate Saturn and Jupiter transit house relative to Ascendant
        swe.set_sid_mode(swe.SIDM_LAHIRI)
        target_jd = swe.julday(target_year_start.year, target_year_start.month, target_year_start.day, 12.0)
        sat_pos = swe.calc_ut(target_jd, swe.SATURN, swe.FLG_SIDEREAL)[0][0]
        jup_pos = swe.calc_ut(target_jd, swe.JUPITER, swe.FLG_SIDEREAL)[0][0]
        
        sat_transit_house = ((int(sat_pos // 30) - asc_sign_idx) % 12) + 1
        jup_transit_house = ((int(jup_pos // 30) - asc_sign_idx) % 12) + 1
        
        ad_interp = interpretations.get(ad, interpretations["Jupiter"])
        
        predictions.append({
            "year_number": year_idx + 1,
            "period": f"{target_year_start.strftime('%b %Y')} – {target_year_end.strftime('%b %Y')}",
            "mahadasha": md,
            "antardasha": ad,
            "saturn_transit_house": sat_transit_house,
            "jupiter_transit_house": jup_transit_house,
            "theme": f"{md}-{ad} Dasha Axis with Jupiter transiting House {jup_transit_house}",
            "career_forecast": f"{ad_interp['career']} Saturn's placement in House {sat_transit_house} emphasizes disciplined accountability.",
            "financial_forecast": f"{ad_interp['finance']} Jupiter in House {jup_transit_house} brings protective grace to family assets.",
            "health_relationship": f"{ad_interp['general']} Mutual cooperation safeguards domestic harmony.",
            "remedies": f"Recite daily mantras dedicated to functional benefic planets ({md} / {ad}) and practice regular morning meditation."
        })

    return predictions

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
@app.post("/api/generate-kundali")
async def generate_kundali_api(req: HoroscopeRequest):
    jd = get_julian_day(req.year, req.month, req.day, req.hour, req.minute, req.second, req.timezone_offset)
    chart_data = calculate_sidereal_positions(jd, req.latitude, req.longitude)
    
    birth_dt = datetime(req.year, req.month, req.day, req.hour, req.minute, req.second)
    timeline = calculate_vimshottari_timeline(
        birth_dt,
        chart_data["moon_nakshatra"]["index"],
        chart_data["moon_nakshatra"]["fraction_elapsed"]
    )
    
    predictions_9yr = generate_9_year_predictions(birth_dt, chart_data, timeline)
    
    return {
        "client_name": req.name,
        "birth_details": {
            "datetime": birth_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "latitude": req.latitude,
            "longitude": req.longitude
        },
        "chart_data": chart_data,
        "predictions_9yr": predictions_9yr
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
