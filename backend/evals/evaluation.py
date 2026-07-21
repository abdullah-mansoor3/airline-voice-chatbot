import asyncio
import os
import time
import json
from dotenv import load_dotenv
load_dotenv(".env")
from typing import Dict, List, Any
from dataclasses import dataclass
from openai import AsyncOpenAI
import statistics
    
# Import the actual agent pipeline
from backend.agent.graph import run_agent_turn

# Configuration
SAMPLE_SIZE = 10  # Evaluate all new test queries
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

@dataclass
class LatencyMetrics:
    total: float
    planning: float
    search_policy: float
    search_flights: float
    web_search: float
    db_retrieval: float
    ttft: float  # Time to First Token (from generation_start)
    tps: float  # Tokens Per Second
    
@dataclass
class QualityMetrics:
    relevance: float  # Score 1-5
    helpfulness: float  # Score 1-5
    coherence: float  # Score 1-5
    hallucination: bool  # True if unsupported claims exist

class Evaluator:
    def __init__(self):
        self.github_client = AsyncOpenAI(
            base_url="https://models.github.ai/inference",
            api_key=GITHUB_TOKEN
        ) if GITHUB_TOKEN else None
        self.test_queries = self._load_test_queries()
    
    def _load_test_queries(self) -> List[str]:
        return [
            # Standard RAG
            "I want to cancel my PIA flight, what do I do?",
            "What is the refund policy for cancelled flights?",
            # Flight Search (Tool execution)
            "Find me flights from LHE to DXB tomorrow.",
            "Are there any business class flights from ISB to KHI next week?",
            # Web Search (Out of domain / recent events)
            "What is the current travel advisory for the UK?",
            "Are there any ongoing strikes at Heathrow airport today?",
            # Order Context (DB read/write simulation)
            "What is the status of my current booking?",
            "Can you change the date of my flight booking?",
            # Ambiguous / Complex multi-tool
            "I lost my bag.",
            "I have a flight from ISB to DXB tomorrow, but I lost my passport. What are the policies and can you find me a flight for next week instead?"
        ]
    
    async def measure_pipeline(self, query: str) -> tuple[LatencyMetrics, str, List[Any]]:
        """Run the actual pipeline and measure latency of each step."""
        timestamps = {'start': time.time()}
        tool_times = {}

        async def token_callback(token):
            if 'first_token' not in timestamps:
                timestamps['first_token'] = time.time()
            timestamps['last_token'] = time.time()
            
        async def debug_callback(entry):
            event_type = entry.get("type")
            t_now = time.time()
            
            if event_type == "planning_complete":
                timestamps['planning_complete'] = t_now
            elif event_type == "tool_start":
                tool = entry.get("tool")
                if tool not in tool_times:
                    tool_times[tool] = {}
                tool_times[tool]['start'] = t_now
            elif event_type == "tool_complete":
                tool = entry.get("tool")
                if tool in tool_times:
                    tool_times[tool]['complete'] = t_now
            elif event_type == "generation_start":
                timestamps['generation_start'] = t_now

        # Run the actual agent
        agent_result = await run_agent_turn(
            query=query,
            language="en",
            user_id="test_user",
            memory_context={},
            token_callback=token_callback,
            debug_callback=debug_callback,
            for_voice=False,
        )
        
        timestamps['end'] = time.time()
        
        # Calculate latencies
        planning_latency = timestamps.get('planning_complete', timestamps['start']) - timestamps['start']
        
        def get_tool_latency(tool_name: str) -> float:
            if tool_name in tool_times and 'start' in tool_times[tool_name] and 'complete' in tool_times[tool_name]:
                return tool_times[tool_name]['complete'] - tool_times[tool_name]['start']
            return 0.0

        gen_start = timestamps.get('generation_start', timestamps['start'])
        first_token = timestamps.get('first_token', gen_start)
        last_token = timestamps.get('last_token', first_token)
        
        ttft = first_token - gen_start
        gen_duration = last_token - first_token
        tps = (len(agent_result.response_text) / 4) / gen_duration if gen_duration > 0 else 0.0
        
        metrics = LatencyMetrics(
            total=timestamps['end'] - timestamps['start'],
            planning=planning_latency,
            search_policy=get_tool_latency('search_policy'),
            search_flights=get_tool_latency('search_alternative_flights'),
            web_search=get_tool_latency('brave_web_search') + get_tool_latency('brave_web_search_fallback'),
            db_retrieval=get_tool_latency('load_order_context'),
            ttft=ttft,
            tps=tps,
        )
        
        return metrics, agent_result.response_text, agent_result.retrieved_chunks
    
    async def evaluate_quality(self, query: str, response: str, retrieved_docs: List[Any]) -> QualityMetrics:
        """Use GitHub Models API (o4-mini) to evaluate response quality."""
        if not self.github_client:
            return QualityMetrics(0, 0, 0, True)
            
        doc_texts = [doc.get("chunk_text", "") for doc in retrieved_docs]
        docs_str = "\n".join(doc_texts)[:2000]
        
        prompt = f"""
        Evaluate this airline assistant response:
        
        Query: {query}
        Response: {response}
        Retrieved Context: {docs_str}
        
        Rate on 1-5 scale:
        1. Relevance: Does the response address the query?
        2. Helpfulness: Is the response useful?
        3. Coherence: Does the response make sense?
        
        Also check: Are all claims in the response supported by the retrieved context?
        
        Return JSON: {{"relevance": X, "helpfulness": X, "coherence": X, "hallucination": bool}}
        """
        
        try:
            completion = await self.github_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "developer", "content": "You are a helpful assistant. Output ONLY valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
            )
            result_text = completion.choices[0].message.content.strip()
            if result_text.startswith("```json"):
                result_text = result_text[7:-3]
            elif result_text.startswith("```"):
                result_text = result_text[3:-3]
                
            result = json.loads(result_text)
            return QualityMetrics(
                relevance=result.get("relevance", 0),
                helpfulness=result.get("helpfulness", 0),
                coherence=result.get("coherence", 0),
                hallucination=result.get("hallucination", True),
            )
        except Exception as e:
            print(f"Eval error: {e}")
            return QualityMetrics(0, 0, 0, True)
    
    async def run_evaluation(self):
        print("Starting full pipeline evaluation...")
        
        latency_results = []
        quality_results = []
        
        print(f"{'Query':<50} | {'Total':<6} | {'Plan':<5} | {'RAG':<5} | {'Web':<5} | {'DB':<5} | {'TTFT':<5} | {'TPS':<5} | {'Rel':<3} | {'Help':<4} | {'Coh':<3} | {'Hallu'}")
        print("-" * 140)
        
        for i, query in enumerate(self.test_queries[:SAMPLE_SIZE]):
            short_query = query[:47] + "..." if len(query) > 50 else query.ljust(50)
            
            # 1. Run Pipeline & Measure Latency
            lat, response, docs = await self.measure_pipeline(query)
            latency_results.append(lat)
            
            # 2. LLM as a Judge Evaluation
            qual = await self.evaluate_quality(query, response, docs)
            quality_results.append(qual)
            
            print(f"{short_query} | {lat.total:5.2f}s | {lat.planning:4.2f}s | {lat.search_policy:4.2f}s | {lat.web_search:4.2f}s | {lat.db_retrieval:4.2f}s | {lat.ttft:4.2f}s | {lat.tps:5.1f} | {qual.relevance:3} | {qual.helpfulness:4} | {qual.coherence:3} | {qual.hallucination}")
        
        # Averages
        avg_lat = LatencyMetrics(
            total=statistics.mean([r.total for r in latency_results]),
            planning=statistics.mean([r.planning for r in latency_results]),
            search_policy=statistics.mean([r.search_policy for r in latency_results]),
            search_flights=statistics.mean([r.search_flights for r in latency_results]),
            web_search=statistics.mean([r.web_search for r in latency_results]),
            db_retrieval=statistics.mean([r.db_retrieval for r in latency_results]),
            ttft=statistics.mean([r.ttft for r in latency_results]),
            tps=statistics.mean([r.tps for r in latency_results]),
        )
        avg_qual = QualityMetrics(
            relevance=statistics.mean([r.relevance for r in quality_results]),
            helpfulness=statistics.mean([r.helpfulness for r in quality_results]),
            coherence=statistics.mean([r.coherence for r in quality_results]),
            hallucination=any(r.hallucination for r in quality_results), # True if any hallucinated
        )
        
        print("-" * 140)
        print(f"{'AVERAGE':<50} | {avg_lat.total:5.2f}s | {avg_lat.planning:4.2f}s | {avg_lat.search_policy:4.2f}s | {avg_lat.web_search:4.2f}s | {avg_lat.db_retrieval:4.2f}s | {avg_lat.ttft:4.2f}s | {avg_lat.tps:5.1f} | {avg_qual.relevance:3.1f} | {avg_qual.helpfulness:4.1f} | {avg_qual.coherence:3.1f} | {avg_qual.hallucination}")
        
        print("\nEvaluation Complete.")
        
`if __name__ == "__main__":
    evaluator = Evaluator()
    asyncio.run(evaluator.run_evaluation())
