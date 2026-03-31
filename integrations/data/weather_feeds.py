"""Weather & Agricultural Data Feeds — Global data integration for prediction markets."""
from __future__ import annotations
import asyncio
import httpx
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import json

logger = logging.getLogger("weather_feeds")


class WeatherIntelligence:
    """Global weather and agricultural data aggregation for prediction advantage."""
    
    def __init__(self):
        self.session = None
        
        # Multiple weather API endpoints for redundancy
        self.apis = {
            "openweather": {
                "url": "https://api.openweathermap.org/data/2.5",
                "key": None  # Will need API key
            },
            "weatherapi": {
                "url": "https://api.weatherapi.com/v1", 
                "key": None  # Alternative source
            },
            "noaa": {
                "url": "https://api.weather.gov",  # US government, free
                "key": None
            }
        }
        
        # Critical agricultural regions for crop predictions
        self.crop_regions = {
            "corn_belt": {
                "name": "US Corn Belt",
                "coords": [(41.8781, -87.6298), (40.7589, -89.6465), (42.0308, -93.6319)],
                "primary_crops": ["corn", "soybeans"],
                "markets": ["corn prices", "soybean futures", "food inflation"]
            },
            "wheat_belt": {
                "name": "Great Plains Wheat",
                "coords": [(39.7392, -104.9903), (37.6872, -97.3301), (46.8083, -100.7837)],
                "primary_crops": ["wheat", "barley"],
                "markets": ["wheat prices", "grain exports", "food security"]
            },
            "ukraine_farming": {
                "name": "Ukrainian Agricultural Region",
                "coords": [(50.4501, 30.5234), (48.3794, 31.1656), (49.9935, 36.2304)],
                "primary_crops": ["wheat", "corn", "sunflower"],
                "markets": ["global grain supply", "food crisis", "ukraine conflict impact"]
            },
            "brazil_soy": {
                "name": "Brazilian Soy Belt",
                "coords": [(-15.7975, -47.8919), (-16.6869, -49.2648), (-14.2350, -51.9253)],
                "primary_crops": ["soybeans", "corn", "cotton"],
                "markets": ["soy exports", "deforestation", "brazil politics"]
            }
        }
        
    async def __aenter__(self):
        self.session = httpx.AsyncClient(timeout=30.0)
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.aclose()
    
    async def get_regional_weather_analysis(self, region: str) -> Dict:
        """Analyze weather conditions for agricultural regions."""
        if region not in self.crop_regions:
            return {"error": f"Unknown region: {region}"}
        
        region_data = self.crop_regions[region]
        weather_summary = []
        
        try:
            # Sample multiple coordinates within region
            for coord in region_data["coords"][:3]:  # Limit to avoid rate limits
                lat, lon = coord
                weather = await self._get_location_weather(lat, lon)
                if weather:
                    weather_summary.append(weather)
            
            if not weather_summary:
                return {"error": "No weather data available"}
            
            # Aggregate regional conditions
            analysis = self._analyze_agricultural_impact(weather_summary, region_data)
            
            return {
                "region": region_data["name"],
                "primary_crops": region_data["primary_crops"],
                "affected_markets": region_data["markets"],
                "weather_analysis": analysis,
                "prediction_signals": self._generate_prediction_signals(analysis, region_data),
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error analyzing region {region}: {e}")
            return {"error": str(e)}
    
    async def _get_location_weather(self, lat: float, lon: float) -> Optional[Dict]:
        """Fetch weather data for specific coordinates."""
        try:
            # Using NOAA (free, US government data)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                # For US locations, use NOAA
                if 25 <= lat <= 49 and -125 <= lon <= -66:
                    return await self._fetch_noaa_weather(lat, lon)
                
            # Fallback: simulate weather data for demo
            return self._generate_sample_weather(lat, lon)
            
        except Exception as e:
            logger.error(f"Error fetching weather for {lat}, {lon}: {e}")
            return None
    
    async def _fetch_noaa_weather(self, lat: float, lon: float) -> Optional[Dict]:
        """Fetch from NOAA API (US locations)."""
        try:
            # Get grid point
            response = await self.session.get(
                f"{self.apis['noaa']['url']}/points/{lat},{lon}"
            )
            response.raise_for_status()
            
            data = response.json()
            forecast_url = data["properties"]["forecast"]
            
            # Get forecast
            forecast_response = await self.session.get(forecast_url)
            forecast_response.raise_for_status()
            
            forecast_data = forecast_response.json()
            periods = forecast_data["properties"]["periods"][:7]  # 7 days
            
            return {
                "source": "NOAA",
                "lat": lat,
                "lon": lon,
                "forecast": periods,
                "alerts": data.get("properties", {}).get("alerts", [])
            }
            
        except Exception as e:
            logger.error(f"NOAA fetch error: {e}")
            return self._generate_sample_weather(lat, lon)
    
    def _generate_sample_weather(self, lat: float, lon: float) -> Dict:
        """Generate representative weather data for analysis."""
        import random
        
        # Seasonal adjustment based on month
        month = datetime.now().month
        
        if 3 <= month <= 5:  # Spring
            temp_base = 15
            precip_chance = 0.4
        elif 6 <= month <= 8:  # Summer  
            temp_base = 25
            precip_chance = 0.3
        elif 9 <= month <= 11:  # Fall
            temp_base = 12
            precip_chance = 0.5
        else:  # Winter
            temp_base = 5
            precip_chance = 0.4
        
        return {
            "source": "simulated",
            "lat": lat,
            "lon": lon,
            "current": {
                "temperature": temp_base + random.uniform(-5, 10),
                "humidity": random.uniform(40, 80),
                "precipitation_mm": random.uniform(0, 20) if random.random() < precip_chance else 0,
                "wind_speed": random.uniform(5, 25),
                "pressure": random.uniform(1000, 1020)
            },
            "forecast_7day": {
                "avg_temp": temp_base + random.uniform(-3, 8),
                "total_precipitation": random.uniform(0, 50),
                "extreme_weather_risk": random.choice(["low", "medium", "high"]),
                "growing_conditions": random.choice(["poor", "fair", "good", "excellent"])
            }
        }
    
    def _analyze_agricultural_impact(self, weather_data: List[Dict], region_info: Dict) -> Dict:
        """Analyze weather impact on agricultural production."""
        if not weather_data:
            return {}
        
        # Aggregate weather metrics
        temperatures = []
        precipitation = []
        extreme_risks = []
        
        for location in weather_data:
            if "current" in location:
                temperatures.append(location["current"]["temperature"])
                precipitation.append(location["current"]["precipitation_mm"])
            
            if "forecast_7day" in location:
                extreme_risks.append(location["forecast_7day"]["extreme_weather_risk"])
        
        avg_temp = sum(temperatures) / len(temperatures) if temperatures else 15
        total_precip = sum(precipitation) if precipitation else 0
        
        # Assess agricultural conditions
        analysis = {
            "temperature_analysis": {
                "average": avg_temp,
                "status": self._assess_temperature_impact(avg_temp, region_info["primary_crops"])
            },
            "precipitation_analysis": {
                "total_mm": total_precip,
                "status": self._assess_precipitation_impact(total_precip, region_info["primary_crops"])
            },
            "overall_conditions": self._calculate_overall_conditions(avg_temp, total_precip),
            "crop_stress_indicators": self._identify_stress_factors(avg_temp, total_precip, extreme_risks),
            "market_impact_probability": self._estimate_market_impact(avg_temp, total_precip, extreme_risks)
        }
        
        return analysis
    
    def _assess_temperature_impact(self, temp: float, crops: List[str]) -> str:
        """Assess temperature impact on specific crops."""
        # Corn optimal: 20-30°C, Soybeans: 20-25°C, Wheat: 15-25°C
        optimal_ranges = {
            "corn": (20, 30),
            "soybeans": (20, 25), 
            "wheat": (15, 25),
            "cotton": (21, 27),
            "barley": (15, 20)
        }
        
        impacts = []
        for crop in crops:
            if crop in optimal_ranges:
                min_temp, max_temp = optimal_ranges[crop]
                if min_temp <= temp <= max_temp:
                    impacts.append("optimal")
                elif temp < min_temp - 5 or temp > max_temp + 5:
                    impacts.append("stress")
                else:
                    impacts.append("suboptimal")
            else:
                impacts.append("unknown")
        
        if "stress" in impacts:
            return "stress_conditions"
        elif "optimal" in impacts:
            return "favorable_conditions" 
        else:
            return "neutral_conditions"
    
    def _assess_precipitation_impact(self, precip: float, crops: List[str]) -> str:
        """Assess precipitation impact on crops."""
        if precip < 5:
            return "drought_risk"
        elif precip > 40:
            return "flood_risk"
        elif 15 <= precip <= 25:
            return "optimal_moisture"
        else:
            return "adequate_moisture"
    
    def _calculate_overall_conditions(self, temp: float, precip: float) -> str:
        """Calculate overall agricultural conditions score."""
        temp_score = 5  # Neutral baseline
        precip_score = 5
        
        # Temperature scoring (5 = optimal)
        if 18 <= temp <= 27:
            temp_score = 5
        elif 15 <= temp < 18 or 27 < temp <= 32:
            temp_score = 4
        elif temp < 10 or temp > 35:
            temp_score = 1
        else:
            temp_score = 3
        
        # Precipitation scoring
        if 15 <= precip <= 25:
            precip_score = 5
        elif 10 <= precip < 15 or 25 < precip <= 35:
            precip_score = 4
        elif precip < 5 or precip > 50:
            precip_score = 1
        else:
            precip_score = 3
        
        overall = (temp_score + precip_score) / 2
        
        if overall >= 4.5:
            return "excellent"
        elif overall >= 3.5:
            return "good"
        elif overall >= 2.5:
            return "fair"
        else:
            return "poor"
    
    def _identify_stress_factors(self, temp: float, precip: float, extreme_risks: List[str]) -> List[str]:
        """Identify stress factors affecting crop production."""
        stress_factors = []
        
        if temp > 32:
            stress_factors.append("heat_stress")
        if temp < 10:
            stress_factors.append("cold_stress")
        if precip < 5:
            stress_factors.append("drought_stress")
        if precip > 40:
            stress_factors.append("waterlog_stress")
        if "high" in extreme_risks:
            stress_factors.append("extreme_weather_risk")
        
        return stress_factors
    
    def _estimate_market_impact(self, temp: float, precip: float, extreme_risks: List[str]) -> float:
        """Estimate probability of significant market impact (0-1)."""
        impact_score = 0.0
        
        # Temperature extremes
        if temp > 35 or temp < 5:
            impact_score += 0.3
        elif temp > 30 or temp < 10:
            impact_score += 0.1
        
        # Precipitation extremes
        if precip > 50 or precip < 2:
            impact_score += 0.3
        elif precip > 35 or precip < 8:
            impact_score += 0.1
        
        # Extreme weather
        high_risk_count = extreme_risks.count("high")
        impact_score += high_risk_count * 0.2
        
        return min(impact_score, 1.0)  # Cap at 100%
    
    def _generate_prediction_signals(self, analysis: Dict, region_info: Dict) -> List[Dict]:
        """Generate specific prediction market signals from weather analysis."""
        signals = []
        
        overall_conditions = analysis.get("overall_conditions", "fair")
        market_impact_prob = analysis.get("market_impact_probability", 0.0)
        stress_factors = analysis.get("crop_stress_indicators", [])
        
        # Generate signals for each affected market
        for market in region_info.get("affected_markets", []):
            signal_strength = self._calculate_signal_strength(
                overall_conditions, market_impact_prob, stress_factors, market
            )
            
            direction = self._determine_market_direction(
                overall_conditions, stress_factors, market
            )
            
            if signal_strength > 0.3:  # Only significant signals
                signals.append({
                    "market_category": market,
                    "signal_strength": signal_strength,
                    "direction": direction,
                    "confidence": min(signal_strength * 0.8, 0.95),
                    "reasoning": self._explain_signal_reasoning(
                        overall_conditions, stress_factors, market, direction
                    )
                })
        
        return signals
    
    def _calculate_signal_strength(self, conditions: str, impact_prob: float, 
                                 stress_factors: List[str], market: str) -> float:
        """Calculate signal strength for specific market."""
        base_strength = {
            "excellent": 0.2,
            "good": 0.1, 
            "fair": 0.05,
            "poor": 0.4
        }.get(conditions, 0.05)
        
        # Boost for high impact probability
        impact_boost = impact_prob * 0.3
        
        # Boost for stress factors
        stress_boost = len(stress_factors) * 0.1
        
        return min(base_strength + impact_boost + stress_boost, 1.0)
    
    def _determine_market_direction(self, conditions: str, stress_factors: List[str], 
                                  market: str) -> str:
        """Determine expected market direction."""
        if any(stress in stress_factors for stress in ["drought_stress", "heat_stress", "extreme_weather_risk"]):
            if "price" in market.lower() or "inflation" in market.lower():
                return "bullish"  # Stress → higher prices
            elif "supply" in market.lower():
                return "bearish"  # Stress → lower supply
        
        if conditions in ["excellent", "good"]:
            if "price" in market.lower():
                return "bearish"  # Good conditions → lower prices
            elif "supply" in market.lower():
                return "bullish"  # Good conditions → higher supply
        
        return "neutral"
    
    def _explain_signal_reasoning(self, conditions: str, stress_factors: List[str], 
                                market: str, direction: str) -> str:
        """Generate human-readable reasoning for signal."""
        if direction == "bullish" and "price" in market.lower():
            return f"Weather stress factors {stress_factors} likely to reduce crop yields, driving prices higher"
        elif direction == "bearish" and "price" in market.lower():
            return f"Favorable conditions ({conditions}) suggest good yields, likely suppressing prices"
        elif direction == "bullish" and "supply" in market.lower():
            return f"Good weather conditions ({conditions}) support strong crop production"
        elif direction == "bearish" and "supply" in market.lower():
            return f"Weather stress {stress_factors} threatens crop production levels"
        else:
            return f"Weather analysis ({conditions}) provides moderate signal for {market}"


async def test_weather_intelligence():
    """Test weather data integration."""
    async with WeatherIntelligence() as weather:
        # Test corn belt analysis
        analysis = await weather.get_regional_weather_analysis("corn_belt")
        
        print("=== CORN BELT WEATHER ANALYSIS ===")
        print(f"Region: {analysis.get('region')}")
        print(f"Crops: {analysis.get('primary_crops')}")
        print(f"Overall Conditions: {analysis.get('weather_analysis', {}).get('overall_conditions')}")
        
        signals = analysis.get('prediction_signals', [])
        print(f"\n=== PREDICTION SIGNALS ({len(signals)} found) ===")
        
        for signal in signals:
            print(f"\nMarket: {signal['market_category']}")
            print(f"Direction: {signal['direction']}")
            print(f"Strength: {signal['signal_strength']:.2f}")
            print(f"Reasoning: {signal['reasoning']}")


if __name__ == "__main__":
    asyncio.run(test_weather_intelligence())