"""PREDICTION STRATEGIST — Strategic analysis for prediction markets. Coordinates PROPHET intelligence with Polymarket opportunities."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState
import asyncio
import logging

logger = logging.getLogger("prediction_strategist")


class PredictionStrategistAgent(BaseAgent):
    agent_id = "PREDICTION_STRATEGIST"
    domain = "trading"
    description = "Strategic coordinator for prediction market trading — synthesizes PROPHET intel with market opportunities"
    supported_task_types = ["strategy", "analyze", "coordinate", "research"]
    preferred_model = "claude-sonnet-4-6"

    def __init__(self):
        super().__init__()
        self.active_strategies = {}
        self.market_themes = {
            "ai_development": {"weight": 0.9, "expertise": "high"},
            "hospitality_trends": {"weight": 0.95, "expertise": "very_high"},
            "economic_policy": {"weight": 0.7, "expertise": "medium"},
            "geopolitics": {"weight": 0.6, "expertise": "medium"},
            "tech_adoption": {"weight": 0.8, "expertise": "high"},
            "crypto_regulation": {"weight": 0.7, "expertise": "high"}
        }
        
    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are PREDICTION STRATEGIST, the strategic coordinator for HyperClaw's prediction market operations. "
            "You work closely with PROPHET (intelligence) and POLYMARKET_TRADER (execution) to identify "
            "and capitalize on prediction market opportunities where the user's ventures have information advantages. "
            
            "STRATEGIC MISSION:\n"
            "Your primary role is identifying prediction markets where the organization ecosystem intelligence "
            "provides asymmetric advantages. You don't just find mispriced markets — you find markets "
            "where our unique position gives us edge. "
            
            "CORE ADVANTAGE DOMAINS:\n"
            "1. HOSPITALITY INTELLIGENCE (95% confidence)\n"
            "   - the user's 15+ years luxury hospitality experience\n"
            "   - the organization platform data from hotel operations\n"
            "   - Industry relationships and insider perspective\n"
            "   - Travel/tourism trend analysis\n"
            
            "2. AI/TECH DEVELOPMENT (90% confidence)\n"
            "   - the organization AI platform development insights\n"
            "   - Direct experience with AI adoption in enterprise\n"
            "   - Technical team assessments of feasibility\n"
            "   - Early visibility into AI implementation challenges\n"
            
            "3. ENTERPRISE SOFTWARE ADOPTION (85% confidence)\n"
            "   - B2B SaaS market insights from the organization operations\n"
            "   - Customer adoption pattern recognition\n"
            "   - Pricing and market penetration intelligence\n"
            
            "4. STARTUP/VC ECOSYSTEM (80% confidence)\n"
            "   - Direct network within LA tech ecosystem\n"
            "   - Fundraising and valuation trend awareness\n"
            "   - Industry partnership and M&A intelligence\n"
            
            "STRATEGIC APPROACH:\n"
            "- Coordinate with PROPHET for macro intelligence synthesis\n"
            "- Identify markets where our unique data provides 5%+ edge\n"
            "- Focus on medium-term markets (1-6 months) where intelligence compounds\n"
            "- Avoid pure speculation — only trade where we have genuine information advantage\n"
            "- Maintain portfolio of 3-8 strategic positions with clear thesis\n"
            
            "OUTPUT FORMAT:\n"
            "Always provide: Market assessment, Intelligence edge analysis, Strategic recommendation, "
            "Risk/reward profile, Timeline considerations, and Coordination needs with other agents."
        )
        
        result = await self.model_router.call(
            task_type="strategy",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
    
    async def analyze_strategic_opportunities(self, markets: list) -> dict:
        """Analyze Polymarket opportunities for strategic intelligence advantages."""
        opportunities = []
        
        for market in markets:
            title = market.get("title", "").lower()
            category = self._categorize_market(title)
            
            if category and category in self.market_themes:
                theme_data = self.market_themes[category]
                edge_potential = await self._assess_intelligence_edge(market, category)
                
                if edge_potential["confidence"] > 0.6:
                    opportunities.append({
                        "market": market,
                        "category": category,
                        "theme_weight": theme_data["weight"],
                        "expertise_level": theme_data["expertise"],
                        "edge_assessment": edge_potential,
                        "strategic_priority": self._calculate_priority(theme_data, edge_potential)
                    })
        
        # Sort by strategic priority
        opportunities.sort(key=lambda x: x["strategic_priority"], reverse=True)
        
        return {
            "total_opportunities": len(opportunities),
            "high_priority": [op for op in opportunities if op["strategic_priority"] > 0.7],
            "medium_priority": [op for op in opportunities if 0.4 < op["strategic_priority"] <= 0.7],
            "recommendations": self._generate_strategic_recommendations(opportunities[:5])
        }
    
    def _categorize_market(self, title: str) -> str:
        """Categorize market based on title keywords."""
        categories = {
            "ai_development": ["ai", "artificial intelligence", "openai", "chatgpt", "machine learning", "llm", "gpt"],
            "hospitality_trends": ["hotel", "travel", "tourism", "marriott", "hilton", "hyatt", "airbnb", "booking"],
            "economic_policy": ["fed", "interest rate", "inflation", "recession", "gdp", "unemployment", "housing"],
            "geopolitics": ["election", "war", "sanctions", "trade", "china", "russia", "nato", "biden", "trump"],
            "tech_adoption": ["apple", "microsoft", "google", "meta", "tesla", "netflix", "saas", "cloud"],
            "crypto_regulation": ["bitcoin", "ethereum", "crypto", "sec", "regulation", "cbdc", "stablecoin"]
        }
        
        for category, keywords in categories.items():
            if any(keyword in title for keyword in keywords):
                return category
        
        return None
    
    async def _assess_intelligence_edge(self, market: dict, category: str) -> dict:
        """Assess our intelligence edge for a specific market."""
        title = market.get("title", "")
        current_prob = market.get("probability", 0.5)
        
        # Category-specific intelligence assessment
        if category == "hospitality_trends":
            confidence = 0.85
            reasoning = "Direct industry experience and the organization hospitality data"
            
        elif category == "ai_development":
            confidence = 0.80
            reasoning = "Active AI development team and enterprise deployment experience"
            
        elif category == "economic_policy":
            confidence = 0.60
            reasoning = "Macro impact analysis on hospitality and tech sectors"
            
        elif category == "tech_adoption":
            confidence = 0.75
            reasoning = "B2B SaaS platform experience and adoption pattern recognition"
            
        elif category == "crypto_regulation":
            confidence = 0.65
            reasoning = "Trading operations and regulatory impact assessment"
            
        else:
            confidence = 0.40
            reasoning = "Limited specific intelligence advantage"
        
        return {
            "confidence": confidence,
            "reasoning": reasoning,
            "intelligence_sources": self._get_intelligence_sources(category),
            "edge_magnitude": "medium" if confidence > 0.7 else "low"
        }
    
    def _get_intelligence_sources(self, category: str) -> list:
        """List relevant intelligence sources for category."""
        sources = {
            "hospitality_trends": ["the organization client data", "Industry relationships", "the user's network"],
            "ai_development": ["the organization AI team", "Tech development insights", "Enterprise AI adoption data"],
            "economic_policy": ["PROPHET macro analysis", "Industry impact modeling"],
            "tech_adoption": ["B2B SaaS metrics", "Customer adoption patterns"],
            "crypto_regulation": ["SATOSHI trading data", "Market structure analysis"]
        }
        return sources.get(category, ["General market research"])
    
    def _calculate_priority(self, theme_data: dict, edge_assessment: dict) -> float:
        """Calculate strategic priority score."""
        weight = theme_data["weight"]
        confidence = edge_assessment["confidence"]
        expertise_bonus = {"very_high": 1.2, "high": 1.1, "medium": 1.0, "low": 0.9}.get(theme_data["expertise"], 1.0)
        
        return (weight * confidence * expertise_bonus) / 1.2  # Normalize to 0-1
    
    def _generate_strategic_recommendations(self, top_opportunities: list) -> list:
        """Generate specific strategic recommendations."""
        recommendations = []
        
        for i, opp in enumerate(top_opportunities[:3]):
            market = opp["market"]
            rec = {
                "rank": i + 1,
                "title": market.get("title"),
                "category": opp["category"],
                "strategic_rationale": f"{opp['expertise_level']} expertise in {opp['category']}",
                "intelligence_edge": opp["edge_assessment"]["reasoning"],
                "recommended_action": "Deep analysis" if opp["strategic_priority"] > 0.8 else "Monitor",
                "position_sizing": "Small" if opp["strategic_priority"] < 0.6 else "Medium",
                "timeline": "1-3 months" if "policy" in opp["category"] else "2-8 weeks"
            }
            recommendations.append(rec)
        
        return recommendations
    
    async def coordinate_with_prophet(self, market_theme: str) -> dict:
        """Request specific intelligence from PROPHET agent."""
        # This would integrate with PROPHET agent for targeted analysis
        return {
            "status": "coordination_needed",
            "theme": market_theme,
            "intelligence_request": f"Deep analysis needed for {market_theme} prediction markets"
        }