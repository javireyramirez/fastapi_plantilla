import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    KeepTogether,
    HRFlowable,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))

        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(
                40,
                letter[1] - 30,
                "SQLAlchemy 2.0 Async — Cheat Sheet & Referencia de Operaciones",
            )
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(40, letter[1] - 35, letter[0] - 40, letter[1] - 35)

        # Footer
        page_text = f"Página {self._pageNumber} de {page_count}"
        self.drawRightString(letter[0] - 40, 25, page_text)
        self.drawString(40, 25, "FastAPI + SQLAlchemy 2.0 (PostgreSQL & AsyncPG)")
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(40, 35, letter[0] - 40, 35)

        self.restoreState()


def build_pdf(filename):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=44,
        bottomMargin=44,
    )

    styles = getSampleStyleSheet()

    # Custom Styles
    primary_color = colors.HexColor("#1E293B")
    accent_color = colors.HexColor("#2563EB")
    code_bg = colors.HexColor("#F8FAFC")

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=primary_color,
        spaceAfter=4,
    )

    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#475569"),
        spaceAfter=12,
    )

    h1_style = ParagraphStyle(
        "Heading1_Custom",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=10,
        spaceAfter=6,
    )

    h2_style = ParagraphStyle(
        "Heading2_Custom",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=accent_color,
        spaceBefore=6,
        spaceAfter=3,
    )

    body_style = ParagraphStyle(
        "Body_Custom",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#334155"),
        spaceAfter=4,
    )

    code_style = ParagraphStyle(
        "Code_Custom",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#0F172A"),
    )

    def make_box(title, explanation, code_snippet):
        content = [
            [
                Paragraph(
                    f"<b>{title}</b> — <font color='#64748B'>{explanation}</font>",
                    body_style,
                )
            ],
            [
                Paragraph(
                    code_snippet.replace("\n", "<br/>").replace(" ", "&nbsp;"),
                    code_style,
                )
            ],
        ]
        t = Table(content, colWidths=[letter[0] - 72])
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                    ("BACKGROUND", (0, 1), (-1, 1), code_bg),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#CBD5E1")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        return t

    story = []

    # Title & Header
    story.append(Paragraph("⚡ SQLAlchemy 2.0 Async — Cheat Sheet", title_style))
    story.append(
        Paragraph(
            "Guía completa de operaciones CRUD básicas, operaciones masivas (Bulk), filtros complejos, relaciones y transacciones en Python.",
            subtitle_style,
        )
    )
    story.append(
        HRFlowable(
            width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=8
        )
    )

    # --- SECCIÓN 1 ---
    story.append(Paragraph("1. Operaciones CRUD Básicas (Async ORM)", h1_style))

    story.append(
        make_box(
            "CREATE (Insertar un registro)",
            "Instancia el modelo, lo añade a la sesión y refresca para obtener el ID / UUID.",
            """user = User(name="Ana", email="ana@ejemplo.com")\nself.session.add(user)\nawait self.session.flush()    # Envía a DB y genera IDs/UUIDv7\nawait self.session.refresh(user)  # Recarga atributos autogenerados\nreturn user""",
        )
    )
    story.append(Spacer(1, 4))

    story.append(
        make_box(
            "READ: Buscar uno solo por clave o filtro (Single)",
            "Usa select().where() con .scalars().first() o .one_or_none()",
            """query = select(User).where(User.email == email)\nresult = await self.session.execute(query)\nuser = result.scalars().first()  # Devuelve User o None""",
        )
    )
    story.append(Spacer(1, 4))

    story.append(
        make_box(
            "READ: Listar múltiples con paginación (Pagination)",
            "Usa .limit() y .offset() con .scalars().all()",
            """query = select(User).where(User.is_active == True).offset(offset).limit(limit)\nresult = await self.session.execute(query)\nusers = list(result.scalars().all())  # Devuelve list[User]""",
        )
    )
    story.append(Spacer(1, 4))

    story.append(
        make_box(
            "UPDATE (Modificar registro existente)",
            "Obtén el objeto mediante SELECT y modifica sus atributos en memoria. SQLAlchemy detecta el cambio.",
            """user = await self.get_user_by_id(user_id)\nif user:\n    user.name = "Nuevo Nombre"\n    user.image = "https://avatar.png"\n    # No requiere session.add(), el Unit of Work lo guarda en el commit/flush""",
        )
    )
    story.append(Spacer(1, 4))

    story.append(
        make_box(
            "DELETE (Eliminar un registro)",
            "Busca el objeto y pásalo a session.delete()",
            """user = await self.get_user_by_id(user_id)\nif user:\n    await self.session.delete(user)""",
        )
    )
    story.append(Spacer(1, 8))

    # --- SECCIÓN 2 ---
    story.append(Paragraph("2. Operaciones Masivas (Bulk Operations)", h1_style))
    story.append(
        Paragraph(
            "Recomendadas para alto rendimiento cuando manejas cientos o miles de registros a la vez.",
            body_style,
        )
    )

    story.append(
        make_box(
            "BULK INSERT: Inserción múltiple de alto rendimiento",
            "Pasa una lista de diccionarios a insert(Model) en una sola llamada SQL.",
            """from sqlalchemy import insert\n\nusers_data = [\n    {"name": "Carlos", "email": "carlos@test.com"},\n    {"name": "Laura", "email": "laura@test.com"},\n]\nawait self.session.execute(insert(User), users_data)""",
        )
    )
    story.append(Spacer(1, 4))

    story.append(
        make_box(
            "BULK UPDATE: Actualización masiva por condición",
            "Actualiza múltiples filas en una sola sentencia SQL sin cargarlas en memoria.",
            """from sqlalchemy import update\n\nquery = (\n    update(User)\n    .where(User.is_active == False)\n    .values(is_active=True, updated_at=func.now())\n)\nawait self.session.execute(query)""",
        )
    )
    story.append(Spacer(1, 4))

    story.append(
        make_box(
            "BULK DELETE: Borrado masivo por condición",
            "Elimina en lote directamente en el motor de base de datos.",
            """from sqlalchemy import delete\n\nquery = delete(Session).where(Session.expires_at < func.now())\nawait self.session.execute(query)""",
        )
    )
    story.append(Spacer(1, 4))

    story.append(
        make_box(
            "UPSERT (Insert or Update en PostgreSQL)",
            "Inserta si no existe, o actualiza campos si hay conflicto en clave única.",
            """from sqlalchemy.dialects.postgresql import insert as pg_insert\n\nstmt = pg_insert(User).values(name="Ana", email="ana@ejemplo.com")\nstmt = stmt.on_conflict_do_update(\n    index_elements=[User.email],\n    set_={"name": stmt.excluded.name, "updated_at": func.now()}\n)\nawait self.session.execute(stmt)""",
        )
    )
    story.append(Spacer(1, 8))

    # --- SECCIÓN 3 ---
    story.append(Paragraph("3. Filtros Complejos & Operadores Lógicos", h1_style))

    story.append(
        make_box(
            "AND, OR y NOT Lógicos",
            "from sqlalchemy import and_, or_, not_",
            """# Múltiples condiciones AND (basta con separarlas por comas en where):\nquery = select(User).where(User.is_active == True, User.is_super_admin == False)\n\n# Condición OR explícita:\nquery = select(User).where(or_(User.email == email, User.name == name))\n\n# Combinación AND + OR + NOT:\nquery = select(User).where(\n    and_(\n        User.is_active == True,\n        not_(User.email.endswith("@spam.com")),\n        or_(User.is_super_admin == True, User.email_verified == True)\n    )\n)""",
        )
    )
    story.append(Spacer(1, 4))

    story.append(
        make_box(
            "IN, NOT IN, LIKE / ILIKE y Buscadores de Texto",
            "Operadores de pertenencia y búsqueda de patrones",
            """# Operador IN / NOT IN:\nquery = select(User).where(User.id.in_([id_1, id_2, id_3]))\nquery = select(Account).where(Account.provider_id.not_in(["google", "github"]))\n\n# Búsqueda insensible a mayúsculas (ILIKE en PostgreSQL):\nquery = select(User).where(User.email.ilike("%@empresa.com"))\n\n# Contains / StartsWith / EndsWith:\nquery = select(User).where(User.name.icontains("javier"))""",
        )
    )
    story.append(Spacer(1, 4))

    story.append(
        make_box(
            "Fechas, Rangos (BETWEEN) y Nulos (IS NULL / IS NOT NULL)",
            "Comparaciones de rango y valores nulos",
            """# Nulos:\nquery = select(User).where(User.image.is_(None))        # IS NULL\nquery = select(User).where(User.image.is_not(None))    # IS NOT NULL\n\n# Rangos con between():\nquery = select(Session).where(Session.created_at.between(fecha_inicio, fecha_fin))\n\n# Comparación directa de fechas:\nquery = select(Session).where(Session.expires_at > func.now())""",
        )
    )
    story.append(Spacer(1, 8))

    # --- SECCIÓN 4 ---
    story.append(
        Paragraph("4. Relaciones & Eager Loading (Evitar problema N+1)", h1_style)
    )

    story.append(
        make_box(
            "JOIN y Carga Ansiosa (joinedload / selectinload)",
            "from sqlalchemy.orm import joinedload, selectinload",
            """# Para relaciones Many-to-One o One-to-One (ej. Session -> User):\n# Realiza un SQL LEFT OUTER JOIN y carga session.user en una sola consulta\nquery = (\n    select(Session)\n    .options(joinedload(Session.user))\n    .where(Session.token == token)\n)\n\n# Para relaciones One-to-Many o Many-to-Many (ej. User -> Sessions / Accounts):\n# Ejecuta un SELECT IN optimizado para cargar listas\nquery = (\n    select(User)\n    .options(selectinload(User.sessions))\n    .where(User.id == user_id)\n)""",
        )
    )
    story.append(Spacer(1, 8))

    # --- SECCIÓN 5 ---
    story.append(Paragraph("5. Agregaciones, Group By & Ordenación", h1_style))

    story.append(
        make_box(
            "COUNT, SUM, AVG, GROUP BY & ORDER BY",
            "from sqlalchemy import func, desc, asc",
            """# Contar total de registros (Count):\nquery = select(func.count(User.id)).where(User.is_active == True)\ntotal = await self.session.scalar(query)\n\n# Ordenar resultados:\nquery = select(User).order_by(desc(User.created_at), asc(User.name))\n\n# Group By con Having (ej. contar sesiones por usuario):\nquery = (\n    select(Session.user_id, func.count(Session.id).label("total_sesiones"))\n    .group_by(Session.user_id)\n    .having(func.count(Session.id) > 3)\n)""",
        )
    )

    # Build Document
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"PDF successfully generated at: {filename}")


if __name__ == "__main__":
    output_path = (
        "z:/home/javir/proyectos/fastapi_plantilla/SQLAlchemy_2_CheatSheet.pdf"
    )
    if not os.path.exists("z:/"):
        output_path = (
            "/home/javir/proyectos/fastapi_plantilla/SQLAlchemy_2_CheatSheet.pdf"
        )
    build_pdf(output_path)
