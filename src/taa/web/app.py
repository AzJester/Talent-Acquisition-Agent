"""Recruiter web app (FastAPI + server-rendered templates)."""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import os
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import ValidationError
from sqlalchemy import select
from yaml import YAMLError

from taa import __version__
from taa.config import Settings, load_settings
from taa.connectors.file_import import load_file
from taa.db import AuditRow, ResultRow, ReviewRow, RubricRow, SearchInputRow, SearchRow, audit, make_session_factory, utcnow
from taa.llm.client import LLMClient, LLMError, make_client
from taa.llm.intake import draft_rubric, rubric_from_yaml, rubric_to_yaml
from taa.llm.outreach import draft_outreach
from taa.models import Rubric, ScoreCard
from taa.query_planner import plan_queries
from taa.service import APPROVE_REASONS, REJECT_REASONS, Services

HERE = Path(__file__).parent
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _seg(score) -> Markup:
    if score is None:
        return Markup('<span class="unk" title="Unknown: no evidence">UNK</span>')
    cells = "".join(f'<i class="{"on" if i < score else ""}"></i>' for i in range(4))
    return Markup(f'<span class="seg" title="{score} of 4" aria-label="{score} of 4">{cells}</span>')


def _dt(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value.replace("T", " ")[:16]
    return value.strftime("%Y-%m-%d %H:%M")


def create_app(settings: Settings | None = None, llm_client: LLMClient | None | str = "auto",
               session_factory=None, pdl_transport=None) -> FastAPI:
    settings = settings or load_settings()
    session_factory = session_factory or make_session_factory(settings.database_url)
    client = make_client(settings) if llm_client == "auto" else llm_client
    svc = Services(settings, session_factory, client, pdl_transport=pdl_transport)
    svc.seed_templates()
    secret = (os.environ.get("TAA_SECRET_KEY") or secrets.token_hex(32)).encode()

    app = FastAPI(title="Astrion Talent Acquisition Agent", version=__version__, docs_url=None, redoc_url=None)
    app.state.services = svc
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["seg"] = _seg
    templates.env.filters["dt"] = _dt
    templates.env.globals.update(version=__version__, reject_reasons=REJECT_REASONS, approve_reasons=APPROVE_REASONS)

    # ---- auth and CSRF -------------------------------------------------------------------------
    def current_user(request: Request) -> str:
        if settings.trusted_user_header:
            u = request.headers.get(settings.trusted_user_header)
            if u:
                return u[:120]
        if settings.app_username and settings.app_password:
            header = request.headers.get("authorization", "")
            if header.lower().startswith("basic "):
                try:
                    user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                except (ValueError, UnicodeDecodeError):
                    user, pw = "", ""
                if secrets.compare_digest(user, settings.app_username) and secrets.compare_digest(pw, settings.app_password):
                    return user
            raise HTTPException(401, "Sign in required", headers={"WWW-Authenticate": 'Basic realm="Astrion TA Agent"'})
        if settings.trusted_user_header:
            raise HTTPException(401, "Sign in through the identity proxy")
        return "local"

    def csrf_for(user: str) -> str:
        return hmac.new(secret, user.encode(), hashlib.sha256).hexdigest()

    def check_csrf(user: str, token: str) -> None:
        if not hmac.compare_digest(csrf_for(user), token or ""):
            raise HTTPException(400, "Form expired or invalid. Reload the page and try again.")

    def render(request: Request, name: str, user: str, nav: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, {
            "user": user, "nav": nav, "csrf": csrf_for(user), "settings": settings,
            "llm_on": svc.llm is not None, "pdl_on": svc.pdl_enabled, **ctx})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 401:
            return PlainTextResponse(str(exc.detail), status_code=401, headers=exc.headers)
        headings = {400: "Request not accepted", 404: "Not found", 409: "Conflict", 413: "Upload too large"}
        return templates.TemplateResponse(request, "error.html", {
            "user": "", "nav": "", "settings": settings, "llm_on": svc.llm is not None, "status": exc.status_code,
            "heading": headings.get(exc.status_code, "Something went wrong"), "message": exc.detail}, status_code=exc.status_code)

    # ---- basic routes -----------------------------------------------------------------------------
    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz():
        return "ok"

    @app.get("/")
    def home(user: str = Depends(current_user)):
        return RedirectResponse("/searches", status_code=303)

    # ---- rubrics ------------------------------------------------------------------------------------
    @app.get("/rubrics", response_class=HTMLResponse)
    def rubrics(request: Request, user: str = Depends(current_user)):
        with svc.Session() as s:
            rows = s.scalars(select(RubricRow).order_by(RubricRow.slug, RubricRow.version.desc())).all()
        return render(request, "rubrics.html", user, "rubrics", rows=rows)

    @app.get("/rubrics/new", response_class=HTMLResponse)
    def rubric_new(request: Request, user: str = Depends(current_user)):
        return render(request, "rubric_new.html", user, "rubrics", error=None, request_text="", yaml_text="")

    @app.post("/rubrics/intake", response_class=HTMLResponse)
    def rubric_intake(request: Request, request_text: str = Form(...), csrf: str = Form(""),
                      user: str = Depends(current_user)):
        check_csrf(user, csrf)
        if svc.llm is None:
            return render(request, "rubric_new.html", user, "rubrics", request_text=request_text, yaml_text="",
                          error="Drafting a rubric from a request needs a model connection. Start from a template or paste YAML.")
        try:
            r = draft_rubric(svc.llm, request_text, svc.signals, settings.drafting_model, "high")
        except (LLMError, ValidationError, ValueError) as e:
            return render(request, "rubric_new.html", user, "rubrics", request_text=request_text, yaml_text="",
                          error=f"Could not draft a rubric: {e}")
        with svc.Session() as s:
            row = RubricRow(slug=r.id, version=1, title=r.title, yaml_text=rubric_to_yaml(r), status="draft",
                            origin="intake", created_by=user)
            s.add(row)
            s.flush()
            audit(s, user, "rubric.drafted", None, rubric_row=row.id, model=settings.drafting_model)
            s.commit()
            rid = row.id
        return RedirectResponse(f"/rubrics/{rid}", status_code=303)

    @app.post("/rubrics", response_class=HTMLResponse)
    def rubric_create(request: Request, yaml_text: str = Form(...), csrf: str = Form(""), user: str = Depends(current_user)):
        check_csrf(user, csrf)
        try:
            r = rubric_from_yaml(yaml_text)
        except (ValidationError, YAMLError, TypeError) as e:
            return render(request, "rubric_new.html", user, "rubrics", request_text="", yaml_text=yaml_text,
                          error=f"Rubric is not valid: {e}")
        with svc.Session() as s:
            row = RubricRow(slug=r.id, version=r.version, title=r.title, yaml_text=yaml_text, status="draft",
                            origin="manual", created_by=user)
            s.add(row)
            s.flush()
            audit(s, user, "rubric.created", None, rubric_row=row.id)
            s.commit()
            rid = row.id
        return RedirectResponse(f"/rubrics/{rid}", status_code=303)

    def _rubric_page(request, user, row, error=None, yaml_text=None):
        parsed, perr = None, None
        try:
            parsed = rubric_from_yaml(row.yaml_text)
        except (ValidationError, YAMLError, TypeError) as e:
            perr = str(e)
        plan = plan_queries(parsed, svc.signals) if parsed else None
        unknown_signals = sorted({sid for c in parsed.criteria for sid in c.signals if sid not in svc.signals.capabilities}) if parsed else []
        return render(request, "rubric_detail.html", user, "rubrics", row=row, rubric=parsed, parse_error=perr,
                      plan=plan, error=error, yaml_text=yaml_text or row.yaml_text, unknown_signals=unknown_signals)

    @app.get("/rubrics/{rid}", response_class=HTMLResponse)
    def rubric_detail(request: Request, rid: int, user: str = Depends(current_user)):
        with svc.Session() as s:
            row = s.get(RubricRow, rid)
            if row is None:
                raise HTTPException(404)
            return _rubric_page(request, user, row)

    @app.post("/rubrics/{rid}", response_class=HTMLResponse)
    def rubric_save(request: Request, rid: int, yaml_text: str = Form(...), csrf: str = Form(""),
                    user: str = Depends(current_user)):
        check_csrf(user, csrf)
        with svc.Session() as s:
            row = s.get(RubricRow, rid)
            if row is None:
                raise HTTPException(404)
            try:
                r = rubric_from_yaml(yaml_text)
            except (ValidationError, YAMLError, TypeError) as e:
                return _rubric_page(request, user, row, error=f"Not saved. Rubric is not valid: {e}", yaml_text=yaml_text)
            if row.status == "approved":
                # Approved rubrics are immutable; an edit becomes a new draft version
                latest = max(v for v in s.scalars(select(RubricRow.version).where(RubricRow.slug == row.slug)))
                data = r.model_dump(mode="json")
                data["version"] = latest + 1
                new = Rubric.model_validate(data)
                text = rubric_to_yaml(new)
                newrow = RubricRow(slug=row.slug, version=new.version, title=new.title, yaml_text=text,
                                   status="draft", origin="edit", created_by=user)
                s.add(newrow)
                s.flush()
                audit(s, user, "rubric.versioned", None, from_row=row.id, to_row=newrow.id, version=new.version)
                s.commit()
                return RedirectResponse(f"/rubrics/{newrow.id}", status_code=303)
            row.yaml_text, row.title = yaml_text, r.title
            audit(s, user, "rubric.edited", None, rubric_row=row.id)
            s.commit()
        return RedirectResponse(f"/rubrics/{rid}", status_code=303)

    @app.post("/rubrics/{rid}/approve")
    def rubric_approve(rid: int, approver: str = Form(...), csrf: str = Form(""), user: str = Depends(current_user)):
        check_csrf(user, csrf)
        approver = approver.strip()
        if not approver:
            raise HTTPException(400, "Approver name is required")
        with svc.Session() as s:
            row = s.get(RubricRow, rid)
            if row is None:
                raise HTTPException(404)
            rubric_from_yaml(row.yaml_text)  # must parse
            row.status, row.approved_by, row.approved_at = "approved", approver[:120], utcnow()
            audit(s, user, "rubric.approved", None, rubric_row=rid, approver=approver)
            s.commit()
        return RedirectResponse(f"/rubrics/{rid}", status_code=303)

    # ---- searches -----------------------------------------------------------------------------------
    @app.get("/searches", response_class=HTMLResponse)
    def searches(request: Request, user: str = Depends(current_user)):
        with svc.Session() as s:
            rows = s.scalars(select(SearchRow).order_by(SearchRow.created_at.desc()).limit(200)).all()
            approved = s.scalar(select(RubricRow.id).where(RubricRow.status == "approved").limit(1))
        return render(request, "searches.html", user, "searches", rows=rows, has_approved=approved is not None)

    @app.get("/searches/new", response_class=HTMLResponse)
    def search_new(request: Request, rubric_id: int | None = None, user: str = Depends(current_user)):
        with svc.Session() as s:
            approved = s.scalars(select(RubricRow).where(RubricRow.status == "approved")
                                 .order_by(RubricRow.title, RubricRow.version.desc())).all()
            selected = next((r for r in approved if r.id == rubric_id), approved[0] if approved else None)
        plan = plan_queries(rubric_from_yaml(selected.yaml_text), svc.signals) if selected else None
        return render(request, "search_new.html", user, "searches", approved=approved, selected=selected, plan=plan, error=None)

    @app.post("/searches")
    async def search_create(request: Request, background: BackgroundTasks, title: str = Form(...), rubric_id: int = Form(...),
                            files: list[UploadFile] = File(default=[]), use_pdl: str = Form(""), pdl_sql: str = Form(""),
                            pdl_size: int = Form(0), csrf: str = Form(""), user: str = Depends(current_user)):
        check_csrf(user, csrf)
        loaded, total_bytes = [], 0
        for f in files:
            if not f.filename:
                continue
            data = await f.read()
            total_bytes += len(data)
            if total_bytes > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "Uploads are limited to 20 MB per search")
            try:
                loaded.append((f.filename, load_file(f.filename, data)))
            except (ValueError, ValidationError, UnicodeDecodeError) as e:
                raise HTTPException(400, f"Could not read {f.filename}: {e}") from e
        wants_pdl = bool(use_pdl) and svc.pdl_enabled and pdl_sql.strip()
        if not loaded and not wants_pdl:
            raise HTTPException(400, "Add at least one candidate file, or enable the People Data Labs search")
        with svc.Session() as s:
            rrow = s.get(RubricRow, rubric_id)
            if rrow is None or rrow.status != "approved":
                raise HTTPException(400, "Searches run only against an approved rubric")
            search = SearchRow(title=title.strip()[:200] or rrow.title, rubric_id=rrow.id, rubric_yaml=rrow.yaml_text,
                               rubric_version=rrow.version, created_by=user,
                               pdl_sql=pdl_sql.strip() if wants_pdl else None,
                               pdl_size=max(1, min(pdl_size or settings.pdl_max_results, settings.pdl_max_results)))
            for name, profiles in loaded:
                search.inputs.append(SearchInputRow(filename=name[:255], source=profiles[0].source if profiles else name[:80],
                                                    record_count=len(profiles),
                                                    profiles=[p.model_dump(mode="json") for p in profiles]))
            s.add(search)
            s.flush()
            audit(s, user, "search.created", search.id, rubric_row=rrow.id, rubric_version=rrow.version,
                  approved_by=rrow.approved_by, files=[n for n, _ in loaded], pdl=bool(wants_pdl))
            s.commit()
            sid = search.id
        background.add_task(svc.execute_search, sid, user)
        return RedirectResponse(f"/searches/{sid}", status_code=303)

    @app.get("/searches/{sid}", response_class=HTMLResponse)
    def search_detail(request: Request, sid: int, show: str = "all", user: str = Depends(current_user)):
        with svc.Session() as s:
            search = s.get(SearchRow, sid)
            if search is None:
                raise HTTPException(404)
            rubric = rubric_from_yaml(search.rubric_yaml)
            rrow = s.get(RubricRow, search.rubric_id)
            results = sorted([r for r in search.results if not r.excluded], key=lambda r: r.rank or 0)
            excluded = [r for r in search.results if r.excluded]
            for r in results:
                _ = r.reviews
            if show == "pending":
                results = [r for r in results if not r.latest_review]
            elif show == "approved":
                results = [r for r in results if r.latest_review and r.latest_review.decision == "approve"]
            plan = plan_queries(rubric, svc.signals)
            reviewed = sum(1 for r in search.results if not r.excluded and r.latest_review)
            approved_n = sum(1 for r in search.results if not r.excluded and r.latest_review and r.latest_review.decision == "approve")
            return render(request, "search_detail.html", user, "searches", search=search, rubric=rubric, rrow=rrow,
                          results=results, excluded=excluded, plan=plan, show=show, reviewed=reviewed,
                          approved_n=approved_n, inputs=list(search.inputs),
                          cards={r.id: ScoreCard.model_validate(r.card) for r in results if r.card})

    @app.post("/searches/{sid}/rerun")
    def search_rerun(sid: int, background: BackgroundTasks, csrf: str = Form(""), user: str = Depends(current_user)):
        check_csrf(user, csrf)
        with svc.Session() as s:
            search = s.get(SearchRow, sid)
            if search is None:
                raise HTTPException(404)
            if search.status == "running":
                raise HTTPException(409, "Search is already running")
            search.status, search.progress = "queued", "Queued for rerun"
            audit(s, user, "search.rerun", sid)
            s.commit()
        background.add_task(svc.execute_search, sid, user)
        return RedirectResponse(f"/searches/{sid}", status_code=303)

    @app.get("/searches/{sid}/export.csv")
    def search_export(sid: int, user: str = Depends(current_user)):
        with svc.Session() as s:
            search = s.get(SearchRow, sid)
            if search is None:
                raise HTTPException(404)
            rubric = rubric_from_yaml(search.rubric_yaml)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["rank", "name", "headline", "location", "current_title", "current_company", "linkedin_url",
                        "total", "coverage", "requirement_unknowns"] + [f"{c.id}_score" for c in rubric.criteria] +
                       [f"{c.id}_evidence" for c in rubric.criteria] + ["flags", "review", "review_reason", "reviewer", "key"])
            for r in sorted([r for r in search.results if not r.excluded], key=lambda r: r.rank or 0):
                card = ScoreCard.model_validate(r.card)
                p = r.profile
                cur = next((x for x in p.get("positions", []) if not x.get("end")), (p.get("positions") or [{}])[0] if p.get("positions") else {})
                lr = r.latest_review
                w.writerow([r.rank, p.get("full_name", ""), p.get("headline", ""), p.get("location", ""),
                            cur.get("title", ""), cur.get("company", ""), p.get("linkedin_url", ""),
                            r.total, r.coverage, r.requirement_unknowns]
                           + [("" if (sc := card.get(c.id)) is None or sc.score is None else sc.score) for c in rubric.criteria]
                           + [" | ".join(sc.evidence) if (sc := card.get(c.id)) else "" for c in rubric.criteria]
                           + ["; ".join(r.flags), lr.decision if lr else "", lr.reason_code if lr else "",
                              lr.reviewer if lr else "", r.key])
            audit(s, user, "search.exported", sid, rows=len(search.results))
            s.commit()
        fname = f"astrion-slate-{sid}-{datetime.now():%Y%m%d}.csv"
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    def _load_result(s, sid: int, rid: int) -> ResultRow:
        r = s.get(ResultRow, rid)
        if r is None or r.search_id != sid:
            raise HTTPException(404)
        return r

    @app.get("/searches/{sid}/results/{rid}", response_class=HTMLResponse)
    def result_detail(request: Request, sid: int, rid: int, user: str = Depends(current_user)):
        with svc.Session() as s:
            r = _load_result(s, sid, rid)
            search = r.search
            rubric = rubric_from_yaml(search.rubric_yaml)
            card = ScoreCard.model_validate(r.card) if r.card else None
            _ = r.reviews
            neighbors = sorted([x for x in search.results if not x.excluded], key=lambda x: x.rank or 0)
            idx = next((i for i, x in enumerate(neighbors) if x.id == r.id), None)
            prev_id = neighbors[idx - 1].id if idx else None
            next_id = neighbors[idx + 1].id if idx is not None and idx + 1 < len(neighbors) else None
            audit(s, user, "candidate.viewed", sid, result=rid)
            s.commit()
            return render(request, "result_detail.html", user, "searches", r=r, search=search, rubric=rubric, card=card,
                          p=r.profile, prev_id=prev_id, next_id=next_id)

    @app.post("/searches/{sid}/results/{rid}/review")
    def result_review(sid: int, rid: int, decision: str = Form(...), reason_code: str = Form(""), note: str = Form(""),
                      csrf: str = Form(""), user: str = Depends(current_user)):
        check_csrf(user, csrf)
        if decision not in {"approve", "reject"}:
            raise HTTPException(400, "Decision must be approve or reject")
        valid = APPROVE_REASONS if decision == "approve" else REJECT_REASONS
        if reason_code not in valid:
            raise HTTPException(400, "Pick a reason code")
        with svc.Session() as s:
            r = _load_result(s, sid, rid)
            r.reviews.append(ReviewRow(decision=decision, reason_code=reason_code, note=note.strip()[:2000], reviewer=user))
            audit(s, user, "candidate.reviewed", sid, result=rid, decision=decision, reason=reason_code)
            s.commit()
            neighbors = sorted([x for x in r.search.results if not x.excluded], key=lambda x: x.rank or 0)
            nxt = next((x.id for x in neighbors if (x.rank or 0) > (r.rank or 0) and not x.latest_review), None)
        return RedirectResponse(f"/searches/{sid}/results/{nxt}" if nxt else f"/searches/{sid}", status_code=303)

    @app.post("/searches/{sid}/results/{rid}/outreach")
    def result_outreach(sid: int, rid: int, csrf: str = Form(""), user: str = Depends(current_user)):
        check_csrf(user, csrf)
        with svc.Session() as s:
            r = _load_result(s, sid, rid)
            rubric = rubric_from_yaml(r.search.rubric_yaml)
            card = ScoreCard.model_validate(r.card)
            try:
                r.outreach_draft = draft_outreach(svc.llm, card, rubric, settings.drafting_model, settings.drafting_effort)
            except LLMError as e:
                r.outreach_draft = f"[Draft failed: {e}]"
            audit(s, user, "outreach.drafted", sid, result=rid, model=settings.drafting_model if svc.llm else "template")
            s.commit()
        return RedirectResponse(f"/searches/{sid}/results/{rid}#outreach", status_code=303)

    # ---- audit ----------------------------------------------------------------------------------------
    @app.get("/audit", response_class=HTMLResponse)
    def audit_log(request: Request, user: str = Depends(current_user)):
        with svc.Session() as s:
            rows = s.scalars(select(AuditRow).order_by(AuditRow.ts.desc()).limit(300)).all()
        return render(request, "audit.html", user, "audit", rows=rows)

    return app
