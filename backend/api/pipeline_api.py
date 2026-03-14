from fastapi import APIRouter, Request
from backend.database import get_session
from backend.pipeline.prop_pipeline import run_prop_pipeline
from backend.models import StrategyModel

router = APIRouter()

@router.post("/run")
async def trigger_pipeline(request: Request):
    session = get_session(request.app.state.engine)
    try:
        prop_strategy = session.query(StrategyModel).filter(
            StrategyModel.strategy_type == "prop",
            StrategyModel.is_active == True,
        ).first()
        strategy_id = prop_strategy.id if prop_strategy else None
        result = await run_prop_pipeline(session, strategy_id=strategy_id)
        return {"status": "completed", **result}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        session.close()
