"""GLOBAL PREDICTION ENGINE — Master coordinator for prediction market trading with multi-source intelligence."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState
import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import json

logger = logging.getLogger("global_prediction_engine")


class GlobalPredictionEngine(BaseAgent):
    agent_id = "GLOBAL_PREDICTION_ENGINE"
    domain = "trading"
    description = "Master prediction market coordinator — synthesizes global intelligence for trading edge"
    supported_task_types = ["predict", "analyze", "coordinate", "trade", "intel"]
    preferred_model = "claude-sonnet-4-6"

    def __init__(self):
        super().__init__()
        
        # Intelligence sources coordination
        self.intelligence_sources = {
            "weather": {"priority": 9, "confidence": 0.85, "active": True},
            "geopolitics": {"priority": 8, "confidence": 0.75, "active": True},
            "economics": {"priority": 9, "confidence": 0.80, "active": True},
            "hospitality": {"priority": 10, "confidence": 0.95, "active": True},
            "tech_adoption": {"priority": 8, "confidence": 0.90, "active": True},
            "social_sentiment": {"priority": 6, "confidence": 0.60, "active": True}
        }
        
        # Strategic advantage domains
        self.advantage_domains = {
            "hospitality": {
                "confidence": 0.95,
                "sources": ["sir_vaughn_expertise", "hyper_nimbus_data", "industry_network"],
                "keywords": ["hotel", "travel", "tourism", "marriott", "hilton", "hyatt", "airbnb", "booking"]
            },
            "ai_development": {
                "confidence": 0.90,
                "sources": ["hyper_nimbus_ai_team", "enterprise_ai_deployment", "tech_development"],
                "keywords": ["ai", "artificial intelligence", "openai", "chatgpt", "machine learning", "llm", "gpt"]
            },
            "b2b_saas": {
                "confidence": 0.85,
                "sources": ["hyper_nimbus_operations", "b2b_market_experience", "saas_adoption_patterns"],
                "keywords": ["saas", "enterprise", "software", "subscription", "platform", "b2b"]
            },
            "startup_ecosystem": {
                "confidence": 0.80,
                "sources": ["la_tech_network", "vc_relationships", "fundraising_intelligence"],
                "keywords": ["startup", "vc", "funding", "valuation", "ipo", "acquisition", "venture"]
            }
        }
        
        # Active trading strategies
        self.active_strategies = {}
        self.position_limits = {
            "max_position_size": 100,  # USD
            "max_daily_exposure": 500,  # USD
            "max_positions": 8,
            "min_edge_threshold": 0.05,  # 5% minimum edge
            "min_confidence": 0.70
        }
        
    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are the GLOBAL PREDICTION ENGINE, the master coordinator for HyperClaw's prediction market operations. "
            "You orchestrate multiple intelligence sources to identify and capitalize on prediction market opportunities "
            "where the user's ecosystem provides asymmetric information advantages. "
            
            "CORE MISSION:\n"
            "Synthesize global intelligence (weather, geopolitics, economics, technology, hospitality) to predict "
            "market movements and execute profitable trades on Polymarket. You are the strategic brain coordinating "
            "PROPHET (intelligence), PREDICTION_STRATEGIST (analysis), and POLYMARKET_TRADER (execution). "
            
            "INTELLIGENCE INTEGRATION:\n"
            "- Weather data → Crop/commodity predictions → Food inflation markets\n"
            "- Geopolitical events → Policy predictions → Economic impact markets\n"
            "- Hospitality intelligence → Travel/tourism markets → Economic indicators\n"
            "- AI development → Technology adoption → Enterprise transformation\n"
            "- Economic indicators → Fed policy → Market volatility\n"
            "- Social sentiment → Political outcomes → Regulatory changes\n"
            
            "STRATEGIC ADVANTAGE FRAMEWORK:\n"
            "1. HOSPITALITY DOMAIN (95% confidence)\n"
            "   - the user's 15+ years luxury hospitality experience\n"
            "   - the organization real-time hotel operations data\n"
            "   - Industry relationships and insider intelligence\n"
            "   - Travel trend analysis and booking pattern recognition\n"
            
            "2. AI/TECH DEVELOPMENT (90% confidence)\n"
            "   - the organization AI platform development insights\n"
            "   - Enterprise AI adoption first-hand experience\n"
            "   - Technical feasibility assessment capability\n"
            "   - Early visibility into implementation challenges\n"
            
            "3. B2B SAAS MARKETS (85% confidence)\n"
            "   - Direct B2B SaaS platform operations experience\n"
            "   - Customer adoption pattern recognition\n"
            "   - Pricing and market penetration intelligence\n"
            "   - Enterprise sales cycle understanding\n"
            
            "4. STARTUP/VC ECOSYSTEM (80% confidence)\n"
            "   - LA tech ecosystem network and relationships\n"
            "   - Fundraising and valuation trend awareness\n"
            "   - M&A and partnership intelligence\n"
            "   - Founder and investor behavior patterns\n"
            
            "PREDICTION METHODOLOGY:\n"
            "- Only trade when edge probability > 5% vs market price\n"
            "- Require confidence > 70% in prediction model\n"
            "- Focus on 1-6 month timeframe markets where intelligence compounds\n"
            "- Prioritize markets with >$1000 daily volume\n"
            "- Maximum $100 per position, $500 daily exposure\n"
            "- Maintain portfolio of 3-8 strategic positions\n"
            
            "OUTPUT REQUIREMENTS:\n"
            "Provide: Intelligence synthesis, Market opportunity assessment, Edge calculation, "
            "Position recommendation, Risk analysis, Execution timeline, and Coordination instructions "
            "for PROPHET, PREDICTION_STRATEGIST, and POLYMARKET_TRADER agents."
        )
        
        result = await self.model_router.call(
            task_type="analysis",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
    
    async def analyze_global_prediction_landscape(self, context: dict = None) -> Dict:
        """Comprehensive analysis of current prediction market opportunities."""
        try:
            # 1. Gather intelligence from all sources
            intelligence_summary = await self._aggregate_intelligence_sources()
            
            # 2. Scan Polymarket for opportunities
            market_opportunities = await self._scan_prediction_markets()
            
            # 3. Match intelligence to market opportunities
            strategic_matches = await self._match_intelligence_to_markets(
                intelligence_summary, market_opportunities
            )
            
            # 4. Calculate trading recommendations
            trading_recommendations = await self._generate_trading_recommendations(
                strategic_matches
            )
            
            # 5. Risk assessment and position sizing
            risk_analysis = await self._assess_portfolio_risk(trading_recommendations)
            
            return {
                "timestamp": datetime.utcnow().isoformat(),
                "intelligence_summary": intelligence_summary,
                "market_scan": {
                    "total_markets": len(market_opportunities),
                    "qualified_opportunities": len(strategic_matches),
                    "high_conviction_trades": len([r for r in trading_recommendations if r.get("conviction", 0) > 0.8])
                },
                "strategic_matches": strategic_matches,
                "trading_recommendations": trading_recommendations,
                "risk_analysis": risk_analysis,
                "execution_plan": self._create_execution_plan(trading_recommendations),
                "agent_coordination": self._coordinate_agent_tasks(trading_recommendations)
            }
            
        except Exception as e:
            logger.error(f"Error in global prediction analysis: {e}")
            return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}
    
    async def _aggregate_intelligence_sources(self) -> Dict:
        """Aggregate intelligence from all active sources."""
        intelligence = {}
        
        # Weather intelligence
        try:
            # This would integrate with WeatherIntelligence class
            intelligence["weather"] = {
                "global_crop_conditions": "monitoring",
                "extreme_weather_alerts": [],
                "agricultural_stress_indicators": [],
                "commodity_impact_signals": []
            }
        except Exception as e:
            logger.error(f"Weather intelligence error: {e}")
        
        # Geopolitical intelligence  
        intelligence["geopolitics"] = {
            "active_conflicts": ["ukraine", "middle_east"],
            "policy_changes": [],
            "sanctions_impact": "moderate",
            "trade_disruptions": []
        }
        
        # Economic intelligence
        intelligence["economics"] = {
            "fed_policy_signals": "neutral",
            "inflation_indicators": "stable",
            "employment_trends": "strong",
            "gdp_projections": "positive"
        }
        
        # Hospitality intelligence (high confidence domain)
        intelligence["hospitality"] = {
            "booking_trends": "strong_recovery",
            "pricing_power": "increasing",
            "capacity_utilization": "85%+",
            "business_travel": "returning",
            "leisure_travel": "exceeding_2019",
            "hyper_nimbus_signals": "positive_adoption"
        }
        
        # Technology intelligence
        intelligence["tech_adoption"] = {
            "ai_enterprise_adoption": "accelerating",
            "saas_market_conditions": "competitive",
            "automation_trends": "strong",
            "digital_transformation": "mature_phase"
        }
        
        return intelligence
    
    async def _scan_prediction_markets(self) -> List[Dict]:
        """Scan Polymarket for relevant opportunities."""
        # This would integrate with PolymarketClient
        # For now, return representative market structure
        
        sample_markets = [
            {
                "condition_id": "sample_1",
                "title": "Will hotel occupancy rates exceed 80% in Q2 2026?",
                "category": "hospitality",
                "current_probability": 0.65,
                "volume_24h": 5000,
                "ends_at": "2026-06-30",
                "advantage_score": 9.5
            },
            {
                "condition_id": "sample_2", 
                "title": "Will the Fed cut rates by 50+ basis points in 2026?",
                "category": "economics",
                "current_probability": 0.35,
                "volume_24h": 15000,
                "ends_at": "2026-12-31",
                "advantage_score": 7.0
            },
            {
                "condition_id": "sample_3",
                "title": "Will enterprise AI adoption exceed 75% by end of 2026?",
                "category": "ai_development",
                "current_probability": 0.45,
                "volume_24h": 8000,
                "ends_at": "2026-12-31", 
                "advantage_score": 9.0
            }
        ]
        
        return sample_markets
    
    async def _match_intelligence_to_markets(self, intelligence: Dict, 
                                           markets: List[Dict]) -> List[Dict]:
        """Match intelligence signals to market opportunities."""
        strategic_matches = []
        
        for market in markets:
            category = market.get("category")
            if category in self.advantage_domains:
                
                # Calculate intelligence edge
                domain_data = self.advantage_domains[category]
                market_prob = market.get("current_probability", 0.5)
                
                # Estimate fair probability based on intelligence
                intelligence_adjustment = self._calculate_intelligence_adjustment(
                    category, intelligence, market
                )
                
                fair_probability = max(0.05, min(0.95, market_prob + intelligence_adjustment))
                edge = fair_probability - market_prob
                
                if abs(edge) >= self.position_limits["min_edge_threshold"]:
                    confidence = domain_data["confidence"] * self._assess_signal_quality(
                        category, intelligence, market
                    )
                    
                    if confidence >= self.position_limits["min_confidence"]:
                        strategic_matches.append({
                            "market": market,
                            "category": category,
                            "intelligence_sources": domain_data["sources"],
                            "current_probability": market_prob,
                            "fair_probability": fair_probability,
                            "edge": edge,
                            "confidence": confidence,
                            "intelligence_signals": self._extract_relevant_signals(
                                category, intelligence, market
                            )
                        })
        
        # Sort by edge * confidence score
        strategic_matches.sort(
            key=lambda x: abs(x["edge"]) * x["confidence"], 
            reverse=True
        )
        
        return strategic_matches
    
    def _calculate_intelligence_adjustment(self, category: str, intelligence: Dict, 
                                        market: Dict) -> float:
        """Calculate probability adjustment based on intelligence."""
        title = market.get("title", "").lower()
        
        if category == "hospitality":
            hospitality_data = intelligence.get("hospitality", {})
            if "hotel" in title and "occupancy" in title:
                if hospitality_data.get("booking_trends") == "strong_recovery":
                    return 0.15  # Bullish adjustment
                elif hospitality_data.get("capacity_utilization", "").startswith("85"):
                    return 0.10
            elif "travel" in title:
                if hospitality_data.get("leisure_travel") == "exceeding_2019":
                    return 0.12
                    
        elif category == "ai_development":
            tech_data = intelligence.get("tech_adoption", {})
            if "ai" in title and "enterprise" in title:
                if tech_data.get("ai_enterprise_adoption") == "accelerating":
                    return 0.18  # Strong bullish signal
                    
        elif category == "economics":
            econ_data = intelligence.get("economics", {})
            if "fed" in title and "rate" in title:
                if econ_data.get("fed_policy_signals") == "dovish":
                    return 0.08
                elif econ_data.get("inflation_indicators") == "declining":
                    return 0.06
        
        return 0.0  # No adjustment if no clear signal
    
    def _assess_signal_quality(self, category: str, intelligence: Dict, 
                             market: Dict) -> float:
        """Assess quality of intelligence signal (0-1 multiplier)."""
        # Base quality from intelligence freshness and reliability
        base_quality = 0.8
        
        # Boost for multiple confirming signals
        confirming_signals = 0
        
        if category == "hospitality":
            hosp_data = intelligence.get("hospitality", {})
            if hosp_data.get("booking_trends") == "strong_recovery":
                confirming_signals += 1
            if hosp_data.get("hyper_nimbus_signals") == "positive_adoption":
                confirming_signals += 1
            if hosp_data.get("pricing_power") == "increasing":
                confirming_signals += 1
                
        elif category == "ai_development":
            tech_data = intelligence.get("tech_adoption", {})
            if tech_data.get("ai_enterprise_adoption") == "accelerating":
                confirming_signals += 1
            if tech_data.get("automation_trends") == "strong":
                confirming_signals += 1
        
        # Each confirming signal boosts quality
        signal_boost = min(confirming_signals * 0.05, 0.15)
        
        return min(base_quality + signal_boost, 1.0)
    
    def _extract_relevant_signals(self, category: str, intelligence: Dict, 
                                market: Dict) -> List[str]:
        """Extract relevant intelligence signals for market."""
        signals = []
        
        if category == "hospitality":
            hosp_data = intelligence.get("hospitality", {})
            for key, value in hosp_data.items():
                if value in ["strong_recovery", "increasing", "positive_adoption", "exceeding_2019"]:
                    signals.append(f"{key}: {value}")
                    
        elif category == "ai_development":
            tech_data = intelligence.get("tech_adoption", {})
            for key, value in tech_data.items():
                if value in ["accelerating", "strong", "mature_phase"]:
                    signals.append(f"{key}: {value}")
        
        return signals
    
    async def _generate_trading_recommendations(self, strategic_matches: List[Dict]) -> List[Dict]:
        """Generate specific trading recommendations."""
        recommendations = []
        
        for match in strategic_matches:
            market = match["market"]
            edge = match["edge"]
            confidence = match["confidence"]
            
            # Kelly criterion for position sizing
            kelly_fraction = (edge * confidence) / abs(edge) if edge != 0 else 0
            position_size = min(
                self.position_limits["max_position_size"],
                kelly_fraction * 200  # Conservative Kelly scaling
            )
            
            if position_size >= 10:  # Minimum position size
                recommendation = {
                    "market_id": market["condition_id"],
                    "title": market["title"],
                    "action": "buy_yes" if edge > 0 else "buy_no",
                    "position_size": position_size,
                    "entry_target": match["fair_probability"],
                    "current_price": match["current_probability"],
                    "edge": edge,
                    "confidence": confidence,
                    "conviction": abs(edge) * confidence,
                    "time_horizon": self._calculate_time_horizon(market),
                    "risk_reward": self._calculate_risk_reward(edge, confidence),
                    "intelligence_basis": match["intelligence_signals"],
                    "stop_loss": self._calculate_stop_loss(match["fair_probability"], edge),
                    "take_profit": self._calculate_take_profit(match["fair_probability"], edge)
                }
                
                recommendations.append(recommendation)
        
        # Sort by conviction score
        recommendations.sort(key=lambda x: x["conviction"], reverse=True)
        
        return recommendations[:self.position_limits["max_positions"]]
    
    def _calculate_time_horizon(self, market: Dict) -> str:
        """Calculate market time horizon."""
        end_date = datetime.fromisoformat(market.get("ends_at", "2026-12-31"))
        time_diff = end_date - datetime.now()
        
        if time_diff.days < 30:
            return "short_term"
        elif time_diff.days < 90:
            return "medium_term"
        else:
            return "long_term"
    
    def _calculate_risk_reward(self, edge: float, confidence: float) -> float:
        """Calculate risk-reward ratio."""
        if edge > 0:
            # Buying yes, risk current price, reward (1 - current_price)
            return (1 - abs(edge)) / abs(edge) if edge != 0 else 1.0
        else:
            # Buying no, risk (1 - current_price), reward current_price  
            return abs(edge) / (1 - abs(edge)) if edge != 1 else 1.0
    
    def _calculate_stop_loss(self, fair_prob: float, edge: float) -> float:
        """Calculate stop loss threshold."""
        if edge > 0:
            return max(0.05, fair_prob - abs(edge) * 0.4)
        else:
            return min(0.95, fair_prob + abs(edge) * 0.4)
    
    def _calculate_take_profit(self, fair_prob: float, edge: float) -> float:
        """Calculate take profit threshold."""
        if edge > 0:
            return min(0.95, fair_prob + abs(edge) * 0.6)
        else:
            return max(0.05, fair_prob - abs(edge) * 0.6)
    
    async def _assess_portfolio_risk(self, recommendations: List[Dict]) -> Dict:
        """Assess overall portfolio risk."""
        total_exposure = sum(r["position_size"] for r in recommendations)
        
        # Concentration risk
        categories = {}
        for rec in recommendations:
            cat = rec.get("category", "unknown")
            categories[cat] = categories.get(cat, 0) + rec["position_size"]
        
        max_category_exposure = max(categories.values()) if categories else 0
        concentration_risk = max_category_exposure / total_exposure if total_exposure > 0 else 0
        
        return {
            "total_exposure": total_exposure,
            "exposure_limit": self.position_limits["max_daily_exposure"],
            "exposure_utilization": total_exposure / self.position_limits["max_daily_exposure"],
            "position_count": len(recommendations),
            "position_limit": self.position_limits["max_positions"],
            "concentration_risk": concentration_risk,
            "category_distribution": categories,
            "risk_score": self._calculate_overall_risk_score(
                total_exposure, concentration_risk, len(recommendations)
            )
        }
    
    def _calculate_overall_risk_score(self, exposure: float, concentration: float, 
                                    position_count: int) -> str:
        """Calculate overall portfolio risk score."""
        risk_factors = 0
        
        if exposure > self.position_limits["max_daily_exposure"] * 0.8:
            risk_factors += 1
        if concentration > 0.6:
            risk_factors += 1
        if position_count > self.position_limits["max_positions"] * 0.8:
            risk_factors += 1
        
        if risk_factors == 0:
            return "low"
        elif risk_factors == 1:
            return "moderate"
        else:
            return "high"
    
    def _create_execution_plan(self, recommendations: List[Dict]) -> Dict:
        """Create step-by-step execution plan."""
        if not recommendations:
            return {"status": "no_trades", "actions": []}
        
        # Prioritize by conviction
        high_conviction = [r for r in recommendations if r["conviction"] > 0.8]
        medium_conviction = [r for r in recommendations if 0.6 < r["conviction"] <= 0.8]
        
        execution_steps = []
        
        # Execute high conviction trades first
        for rec in high_conviction[:3]:  # Limit to top 3
            execution_steps.append({
                "step": len(execution_steps) + 1,
                "action": "execute_trade",
                "market_id": rec["market_id"],
                "priority": "high",
                "timing": "immediate"
            })
        
        # Queue medium conviction trades
        for rec in medium_conviction[:2]:  # Add 2 medium conviction
            execution_steps.append({
                "step": len(execution_steps) + 1,
                "action": "execute_trade", 
                "market_id": rec["market_id"],
                "priority": "medium",
                "timing": "after_high_priority"
            })
        
        return {
            "status": "ready" if execution_steps else "no_trades",
            "total_trades": len(execution_steps),
            "execution_steps": execution_steps,
            "estimated_duration": f"{len(execution_steps) * 2} minutes"
        }
    
    def _coordinate_agent_tasks(self, recommendations: List[Dict]) -> Dict:
        """Coordinate tasks for other agents."""
        return {
            "PROPHET": {
                "task": "Continue monitoring intelligence sources",
                "focus": ["weather_alerts", "geopolitical_developments", "economic_indicators"],
                "update_frequency": "hourly"
            },
            "PREDICTION_STRATEGIST": {
                "task": "Analyze market entry timing",
                "focus": [r["market_id"] for r in recommendations],
                "update_frequency": "every_30_minutes"
            },
            "POLYMARKET_TRADER": {
                "task": "Execute trading recommendations",
                "trades": recommendations,
                "execution_order": "conviction_descending"
            },
            "RISK_MANAGER": {
                "task": "Monitor position risk and portfolio exposure",
                "alert_thresholds": {
                    "daily_loss": 200,
                    "position_loss": 50,
                    "concentration": 0.7
                }
            }
        }


async def test_global_prediction_engine():
    """Test the global prediction engine."""
    engine = GlobalPredictionEngine()
    
    # Mock HyperState
    from unittest.mock import MagicMock
    mock_state = MagicMock()
    
    print("=== GLOBAL PREDICTION ENGINE TEST ===")
    
    analysis = await engine.analyze_global_prediction_landscape()
    
    print(f"Market Analysis Timestamp: {analysis.get('timestamp')}")
    print(f"Total Markets Scanned: {analysis.get('market_scan', {}).get('total_markets')}")
    print(f"Qualified Opportunities: {analysis.get('market_scan', {}).get('qualified_opportunities')}")
    print(f"High Conviction Trades: {analysis.get('market_scan', {}).get('high_conviction_trades')}")
    
    recommendations = analysis.get('trading_recommendations', [])
    print(f"\n=== TRADING RECOMMENDATIONS ({len(recommendations)}) ===")
    
    for i, rec in enumerate(recommendations[:3], 1):
        print(f"\n{i}. {rec['title'][:60]}...")
        print(f"   Action: {rec['action']} | Size: ${rec['position_size']}")
        print(f"   Edge: {rec['edge']:.3f} | Confidence: {rec['confidence']:.2f}")
        print(f"   Conviction: {rec['conviction']:.2f}")


if __name__ == "__main__":
    asyncio.run(test_global_prediction_engine())