from database.db import db
from database.models import Summary


def create_summary(
    *,
    user_id: int,
    summary: str,
    category: str,
    file_name: str | None = None,
    input_text: str | None = None,
    stats: dict | None = None,
) -> Summary:
    record = Summary(
        user_id=user_id,
        input_text=input_text,
        file_name=file_name,
        summary=summary,
        category=category,
    )
    record.set_stats_dict(stats)
    db.session.add(record)
    db.session.commit()
    return record


def get_summary(summary_id: int, user_id: int | None = None) -> Summary | None:
    query = Summary.query.filter_by(id=summary_id)
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    return query.first()


def get_user_summaries(user_id: int, limit: int | None = None) -> list[Summary]:
    query = Summary.query.filter_by(user_id=user_id).order_by(Summary.created_at.desc())
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def delete_summary(summary_id: int, user_id: int | None = None) -> bool:
    record = get_summary(summary_id, user_id=user_id)
    if record is None:
        return False

    db.session.delete(record)
    db.session.commit()
    return True
