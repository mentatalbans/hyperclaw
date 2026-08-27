"""Polymarket API Client — Interface for prediction market data and trading."""
from __future__ import annotations
import asyncio
import httpx
import logging
from typing import Dict, List, Optional, Any
from web3 import Web3
from eth_account import Account
import json
from datetime import datetime

logger = logging.getLogger("polymarket_client")


class PolymarketClient:
    """Client for Polymarket API interactions."""
    
    def __init__(self, private_key: str = None):
        self.base_url = "https://clob.polymarket.com"
        self.data_url = "https://gamma-api.polymarket.com"
        self.private_key = private_key
        self.address = None
        
        if private_key:
            account = Account.from_key(private_key)
            self.address = account.address
            
        self.session = None
        
    async def __aenter__(self):
        self.session = httpx.AsyncClient(timeout=30.0)
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.aclose()
    
    async def get_markets(self, limit: int = 50, active: bool = True) -> List[Dict]:
        """Fetch active prediction markets."""
        try:
            params = {
                "limit": limit,
                "active": str(active).lower(),
                "order": "volume24hr",
                "order_direction": "desc"
            }
            
            response = await self.session.get(
                f"{self.data_url}/markets",
                params=params
            )
            response.raise_for_status()
            
            data = response.json()
            return data.get("data", [])
            
        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return []
    
    async def get_market_data(self, condition_id: str) -> Optional[Dict]:
        """Get detailed data for a specific market."""
        try:
            response = await self.session.get(
                f"{self.data_url}/markets/{condition_id}"
            )
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.error(f"Error fetching market {condition_id}: {e}")
            return None
    
    async def get_market_orderbook(self, token_id: str) -> Optional[Dict]:
        """Get order book for a market token."""
        try:
            response = await self.session.get(
                f"{self.base_url}/book",
                params={"token_id": token_id}
            )
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.error(f"Error fetching orderbook for {token_id}: {e}")
            return None
    
    async def get_market_trades(self, condition_id: str, limit: int = 100) -> List[Dict]:
        """Get recent trades for a market."""
        try:
            params = {
                "condition_id": condition_id,
                "limit": limit
            }
            
            response = await self.session.get(
                f"{self.data_url}/trades",
                params=params
            )
            response.raise_for_status()
            
            data = response.json()
            return data.get("data", [])
            
        except Exception as e:
            logger.error(f"Error fetching trades for {condition_id}: {e}")
            return []
    
    def analyze_market_opportunity(self, market: Dict) -> Dict:
        """Analyze a market for trading opportunities."""
        try:
            title = market.get("question", "").lower()
            volume_24h = float(market.get("volume24hr", 0))
            
            # Calculate basic metrics
            outcomes = market.get("outcomes", [])
            if len(outcomes) >= 2:
                yes_price = float(outcomes[0].get("price", 0.5))
                no_price = float(outcomes[1].get("price", 0.5))
                
                implied_prob = yes_price
                liquidity_score = min(volume_24h / 1000, 10)  # Scale 0-10
                
                # Category classification for advantage assessment
                category = self._classify_market(title)
                advantage_score = self._assess_advantage(category)
                
                return {
                    "condition_id": market.get("condition_id"),
                    "title": market.get("question"),
                    "category": category,
                    "implied_probability": implied_prob,
                    "volume_24h": volume_24h,
                    "liquidity_score": liquidity_score,
                    "advantage_score": advantage_score,
                    "opportunity_score": (liquidity_score * advantage_score) / 10,
                    "analysis_time": datetime.utcnow().isoformat(),
                    "recommendation": self._generate_recommendation(
                        category, advantage_score, liquidity_score, implied_prob
                    )
                }
            
        except Exception as e:
            logger.error(f"Error analyzing market: {e}")
            return {}
    
    def _classify_market(self, title: str) -> str:
        """Classify market by category."""
        title = title.lower()
        
        categories = {
            "hospitality": ["hotel", "travel", "tourism", "marriott", "hilton", "hyatt", "airbnb"],
            "ai_tech": ["ai", "artificial intelligence", "openai", "chatgpt", "machine learning", "llm"],
            "economics": ["fed", "interest rate", "inflation", "recession", "gdp", "unemployment"],
            "politics": ["election", "biden", "trump", "congress", "senate", "vote"],
            "crypto": ["bitcoin", "ethereum", "crypto", "btc", "eth", "blockchain"],
            "business": ["apple", "microsoft", "tesla", "meta", "google", "amazon"],
            "geopolitics": ["war", "ukraine", "russia", "china", "nato", "sanctions"],
            "weather": ["hurricane", "drought", "flood", "climate", "temperature"],
            "sports": ["nfl", "nba", "mlb", "olympics", "world cup", "super bowl"]
        }
        
        for category, keywords in categories.items():
            if any(keyword in title for keyword in keywords):
                return category
        
        return "other"
    
    def _assess_advantage(self, category: str) -> float:
        """Assess our information advantage for this category (0-10)."""
        advantages = {
            "hospitality": 9.5,  # the user's direct expertise
            "ai_tech": 9.0,      # the organization development insights
            "business": 8.0,     # B2B SaaS experience
            "economics": 7.0,    # Impact on all ventures
            "crypto": 7.5,       # Trading experience
            "politics": 6.0,     # General analysis
            "geopolitics": 6.5,  # Global intelligence feeds
            "weather": 8.0,      # Data integration advantage
            "sports": 5.0,       # Limited advantage
            "other": 4.0         # Baseline
        }
        
        return advantages.get(category, 4.0)
    
    def _generate_recommendation(self, category: str, advantage: float, 
                               liquidity: float, prob: float) -> str:
        """Generate trading recommendation."""
        if advantage < 6.0:
            return "SKIP - Limited information advantage"
        
        if liquidity < 3.0:
            return "SKIP - Insufficient liquidity"
        
        if advantage >= 8.0 and liquidity >= 5.0:
            return "HIGH - Strong advantage and liquidity"
        elif advantage >= 7.0 and liquidity >= 3.0:
            return "MEDIUM - Good opportunity for analysis"
        else:
            return "LOW - Monitor for better entry"
    
    async def get_portfolio_summary(self) -> Dict:
        """Get current portfolio summary."""
        if not self.address:
            return {"error": "No wallet address configured"}
        
        try:
            response = await self.session.get(
                f"{self.data_url}/positions",
                params={"user": self.address}
            )
            response.raise_for_status()
            
            data = response.json()
            positions = data.get("data", [])
            
            total_value = sum(float(p.get("size", 0)) * float(p.get("price", 0)) for p in positions)
            
            return {
                "address": self.address,
                "active_positions": len(positions),
                "total_portfolio_value": total_value,
                "positions": positions,
                "last_updated": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error fetching portfolio: {e}")
            return {"error": str(e)}


async def test_polymarket_connection():
    """Test Polymarket API connection."""
    async with PolymarketClient() as client:
        markets = await client.get_markets(limit=5)
        
        print(f"Fetched {len(markets)} markets")
        for market in markets[:3]:
            analysis = client.analyze_market_opportunity(market)
            print(f"\nMarket: {analysis.get('title', 'Unknown')}")
            print(f"Category: {analysis.get('category')}")
            print(f"Advantage Score: {analysis.get('advantage_score'):.1f}/10")
            print(f"Recommendation: {analysis.get('recommendation')}")


if __name__ == "__main__":
    asyncio.run(test_polymarket_connection())