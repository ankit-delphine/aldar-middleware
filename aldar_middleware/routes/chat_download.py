"""Chat export and download endpoints."""

import json
from typing import List, Dict, Any, Optional, Literal
from uuid import UUID
from datetime import datetime
from io import BytesIO
from textwrap import wrap
from urllib.parse import quote
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from azure.core.exceptions import AzureError
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from PIL import Image

try:
    from svglib.svglib import svg2rlg
    SVG_SUPPORT = True
except ImportError:
    SVG_SUPPORT = False

from aldar_middleware.auth.dependencies import get_current_user
from aldar_middleware.database.base import get_db
from aldar_middleware.models.attachment import Attachment
from aldar_middleware.models.sessions import Session
from aldar_middleware.models.user import User
from aldar_middleware.orchestration.blob_storage import BlobStorageService
from aldar_middleware.routes.chat import get_chat_messages_by_session
from aldar_middleware.settings.settings import settings
from aldar_middleware.settings.context import get_correlation_id
from aldar_middleware.monitoring.chat_cosmos_logger import (
    log_conversation_download,
    log_conversation_share,
)
from loguru import logger

router = APIRouter()


async def _collect_session_messages_for_export(
    http_request: Request,
    session_id: UUID,
    current_user: User,
    db: AsyncSession,
    include_system: bool = True,
) -> List[Dict[str, Any]]:
    """Collect all messages for a session by paging through the existing messages endpoint."""
    all_messages: List[Dict[str, Any]] = []
    before_message_uuid: Optional[UUID] = None
    seen_oldest_ids: set[str] = set()

    while True:
        response = await get_chat_messages_by_session(
            http_request=http_request,
            session_id=session_id,
            limit=20,
            before_message_id=before_message_uuid,
            include_system=include_system,
            current_user=current_user,
            db=db,
        )

        chunk = response.get("messages") or []
        if not chunk:
            break

        # Prepend to maintain chronological ordering (oldest first)
        all_messages = chunk + all_messages

        if not response.get("has_more"):
            break

        oldest_id = response.get("oldest_message_id")
        if not oldest_id:
            break

        if oldest_id in seen_oldest_ids:
            # Prevent potential infinite loops if the underlying endpoint repeats IDs
            break
        seen_oldest_ids.add(oldest_id)

        try:
            before_message_uuid = UUID(str(oldest_id))
        except (ValueError, TypeError):
            break

    return all_messages


@router.get("/sessions/{session_id}/export")
async def export_chat_session(
    http_request: Request,
    session_id: UUID,
    response_format: Literal["json", "pdf"] = Query(
        "json",
        description="Export format for the chat transcript.",
    ),
    flag: Optional[str] = Query(
        None,
        description="Set to 'share' to upload the export to attachments storage instead of downloading.",
    ),
    share_visibility: Literal["public", "private"] = Query(
        "private",
        description="When sharing, choose 'public' for an open-access link or 'private' for restricted access.",
    ),
    exclude_key: Optional[str] = Query(
        None,
        description="Optional key to exclude from each message in the exported output.",
    ),
    include_system: bool = Query(
        False,
        description="Include system messages in the exported transcript.",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Export a chat session transcript as JSON or PDF."""
    result = await db.execute(select(Session).where(Session.id == UUID(str(session_id))))
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found",
        )

    if str(session.user_id) != str(current_user.id) and not getattr(current_user, "is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    messages = await _collect_session_messages_for_export(
        http_request=http_request,
        session_id=session_id,
        current_user=current_user,
        db=db,
        include_system=include_system,
    )

    export_messages: List[Dict[str, Any]] = []
    for message in messages:
        message_copy = dict(message)
        if exclude_key:
            message_copy.pop(exclude_key, None)
        export_messages.append(message_copy)

    exported_at = datetime.utcnow().isoformat()
    session_title = session.session_name or "Chat Session"

    export_payload = {
        "session_id": session.session_id,
        "session_title": session_title,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "exported_at": exported_at,
        "message_count": len(export_messages),
        "messages": export_messages,
        "exclude_key": exclude_key,
        "include_system": include_system,
    }

    filename_base = f"chat_{session.session_id}"
    is_share = (flag or "").lower() == "share"
    file_extension = "json" if response_format == "json" else "pdf"
    content_type = "application/json" if response_format == "json" else "application/pdf"
    file_name = f"{filename_base}.{file_extension}"

    # Generate export bytes
    if response_format == "json":
        file_bytes = json.dumps(export_payload, indent=2, default=str).encode("utf-8")
    else:
        buffer = BytesIO()
        pdf_canvas = canvas.Canvas(buffer, pagesize=letter)
        page_width, page_height = letter
        
        # Modern color scheme
        primary_color = colors.HexColor("#2563eb")  # Vibrant blue
        primary_dark = colors.HexColor("#1e40af")  # Dark blue
        user_bubble_color = colors.HexColor("#3b82f6")  # Bright blue
        user_bubble_dark = colors.HexColor("#2563eb")  # Darker blue for depth
        assistant_bubble_color = colors.HexColor("#f8fafc")  # Very light gray
        assistant_bubble_border = colors.HexColor("#e2e8f0")  # Light border
        text_color = colors.HexColor("#1e293b")  # Dark slate
        text_secondary = colors.HexColor("#64748b")  # Medium gray
        accent_color = colors.HexColor("#10b981")  # Green accent
        header_bg = colors.HexColor("#0f172a")  # Very dark blue/black
        white = colors.white
        
        # Margins
        margin_left = 1 * inch
        margin_right = 1 * inch
        margin_top = 1 * inch
        margin_bottom = 0.75 * inch
        content_width = page_width - margin_left - margin_right
        
        # Track page number
        page_num = [1]
        
        # Current Y position
        y_position = page_height - margin_top
        
        def format_timestamp(timestamp: str) -> str:
            """Format timestamp nicely."""
            if not timestamp:
                return ""
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                return dt.strftime("%b %d, %Y at %I:%M %p")
            except Exception:
                return timestamp
        
        def clean_content(content: str) -> str:
            """Clean and prepare content for display."""
            if not content:
                return "(no content)"
            
            cleaned_content = []
            skip_block = False
            for raw_line in content.splitlines():
                stripped = raw_line.strip()
                if stripped.startswith("<additional context>"):
                    skip_block = True
                    continue
                if stripped.endswith("</additional context>"):
                    skip_block = False
                    continue
                if skip_block:
                    continue
                cleaned_content.append(raw_line)
            
            cleaned_text = "\n".join(cleaned_content).strip()
            return cleaned_text if cleaned_text else "(no content)"
        
        def draw_header(is_first_page: bool = True) -> None:
            """Draw a beautiful modern header."""
            nonlocal y_position
            
            if not is_first_page:
                return
            
            header_height = 1.5 * inch
            
            # Gradient-like header background with subtle pattern
            pdf_canvas.setFillColor(header_bg)
            pdf_canvas.rect(0, y_position - header_height, page_width, header_height, fill=1, stroke=0)
            
            # Accent line at top
            pdf_canvas.setFillColor(primary_color)
            pdf_canvas.rect(0, y_position - header_height, page_width, 0.05 * inch, fill=1, stroke=0)
            
            # Load and draw logo instead of "Chat Export" text
            try:
                # Get the path to the logo file
                assets_dir = Path(__file__).parent.parent / "assets"
                logo_path_svg = assets_dir / "aiq_logo.svg"
                logo_path_png = assets_dir / "aiq_logo.png"
                
                logo_drawn = False
                logo_height = 0.7 * inch  # Logo height
                logo_width = None  # Will be calculated to maintain aspect ratio
                logo_y_position = None  # Will store logo Y position for subtitle placement
                corner_radius_px = 15  # Rounded corner radius in pixels for the mask
                
                def create_rounded_mask(size, radius):
                    """Create a rounded rectangle mask for the image."""
                    from PIL import ImageDraw
                    mask = Image.new('L', size, 0)
                    draw = ImageDraw.Draw(mask)
                    draw.rounded_rectangle([(0, 0), size], radius=radius, fill=255)
                    return mask
                
                # Try PNG first (simpler)
                if logo_path_png.exists():
                    try:
                        img = Image.open(logo_path_png)
                        original_size = img.size
                        
                        # Calculate width to maintain aspect ratio
                        aspect_ratio = img.width / img.height
                        logo_width = logo_height * aspect_ratio
                        
                        # Resize image to target size (convert inches to pixels at 150 DPI for better quality)
                        dpi = 150
                        target_size = (int(logo_width * dpi), int(logo_height * dpi))
                        img = img.resize(target_size, Image.Resampling.LANCZOS)
                        
                        # Convert to RGBA if necessary for mask application
                        if img.mode != 'RGBA':
                            img = img.convert('RGBA')
                        
                        # Create rounded corner mask
                        mask = create_rounded_mask(img.size, corner_radius_px)
                        
                        # Apply mask to image
                        img.putalpha(mask)
                        
                        # Convert to RGB with white background for PDF
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        background.paste(img, mask=img.split()[-1])  # Use alpha channel as mask
                        img = background
                        
                        # Save to BytesIO for ReportLab
                        img_buffer = BytesIO()
                        img.save(img_buffer, format='PNG')
                        img_buffer.seek(0)
                        
                        # Draw logo with white box background - center it vertically in the header
                        logo_y_position = y_position - (header_height / 2) - (logo_height / 2)
                        logo_x_position = margin_left
                        
                        # Draw white box background for logo
                        box_padding = 0.1 * inch
                        box_width = logo_width + (box_padding * 2)
                        box_height = logo_height + (box_padding * 2)
                        box_x = logo_x_position - box_padding
                        box_y = logo_y_position - box_padding
                        
                        pdf_canvas.setFillColor(white)
                        pdf_canvas.setStrokeColor(white)
                        pdf_canvas.setLineWidth(0)
                        pdf_canvas.roundRect(box_x, box_y, box_width, box_height, 8, fill=1, stroke=0)
                        
                        # Draw logo on top of white box
                        pdf_canvas.drawImage(img_buffer, logo_x_position, logo_y_position, width=logo_width, height=logo_height, mask='auto')
                        logo_drawn = True
                    except Exception as e:
                        logger.warning(f"Failed to load PNG logo: {e}")
                
                # Try SVG if PNG not available and svglib is installed
                if not logo_drawn and SVG_SUPPORT and logo_path_svg.exists():
                    try:
                        drawing = svg2rlg(str(logo_path_svg))
                        if drawing:
                            # Calculate scale to fit desired height
                            scale = logo_height / drawing.height
                            logo_width = drawing.width * scale
                            
                            # Draw SVG with white box background - center it vertically in the header
                            logo_y_position = y_position - (header_height / 2) - (logo_height / 2)
                            logo_x_position = margin_left
                            
                            # Draw white box background for logo
                            box_padding = 0.1 * inch
                            box_width = logo_width + (box_padding * 2)
                            box_height = logo_height + (box_padding * 2)
                            box_x = logo_x_position - box_padding
                            box_y = logo_y_position - box_padding
                            
                            pdf_canvas.setFillColor(white)
                            pdf_canvas.setStrokeColor(white)
                            pdf_canvas.setLineWidth(0)
                            pdf_canvas.roundRect(box_x, box_y, box_width, box_height, 8, fill=1, stroke=0)
                            
                            # Draw SVG on top of white box
                            pdf_canvas.saveState()
                            pdf_canvas.translate(logo_x_position, logo_y_position)
                            pdf_canvas.scale(scale, scale)
                            drawing.drawOn(pdf_canvas, 0, 0)
                            pdf_canvas.restoreState()
                            logo_drawn = True
                    except Exception as e:
                        logger.warning(f"Failed to load SVG logo: {e}")
                
                # Fallback to text if logo not available
                if not logo_drawn:
                    pdf_canvas.setFillColor(white)
                    pdf_canvas.setFont("Helvetica-Bold", 32)
                    logo_y_position = y_position - 0.5 * inch
                    pdf_canvas.drawString(margin_left, logo_y_position, "Chat Export")
                    # Set logo_width for spacing calculation
                    logo_width = pdf_canvas.stringWidth("Chat Export", "Helvetica-Bold", 32)
            except Exception as e:
                logger.error(f"Error loading logo: {e}")
                # Fallback to text
                pdf_canvas.setFillColor(white)
                pdf_canvas.setFont("Helvetica-Bold", 32)
                logo_y_position = y_position - 0.5 * inch
                pdf_canvas.drawString(margin_left, logo_y_position, "Chat Export")
                logo_drawn = False
                logo_width = None
            
            # Draw session information on the right side of header (left-aligned, starting after logo)
            # Calculate header_top first (always needed)
            header_top = y_position - header_height
            
            # Position text to align with logo vertically
            if logo_drawn and logo_y_position is not None:
                # Align text with logo (logo is vertically centered in header)
                info_start_y = logo_y_position + logo_height - 0.05 * inch  # Start from top of logo area
            else:
                # Fallback if logo not drawn
                info_start_y = header_top + 0.1 * inch
            
            line_spacing = 0.16 * inch
            font_size_small = 9
            
            # Calculate starting X position (after logo white box with spacing)
            box_padding = 0.1 * inch
            logo_end_x = margin_left + (logo_width if logo_width else 0) + (box_padding * 2) + 0.5 * inch  # Increased spacing
            
            # Title (first line, left-aligned)
            pdf_canvas.setFillColor(white)
            pdf_canvas.setFont("Helvetica-Bold", 11)
            display_title = session_title if len(session_title) <= 50 else session_title[:47] + "..."
            title_text = f"Title : {display_title}"
            pdf_canvas.drawString(logo_end_x, info_start_y, title_text)
            
            # Session ID (second line, left-aligned)
            info_y = info_start_y - line_spacing
            pdf_canvas.setFillColor(white)
            pdf_canvas.setFont("Helvetica", font_size_small)
            session_id_text = f"Session ID: {session.session_id}"
            pdf_canvas.drawString(logo_end_x, info_y, session_id_text)
            
            # Exported date (third line, left-aligned)
            info_y = info_y - line_spacing
            try:
                exported_dt = datetime.fromisoformat(exported_at.replace("Z", "+00:00"))
                exported_formatted = exported_dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                exported_formatted = exported_at
            
            pdf_canvas.setFillColor(white)
            pdf_canvas.setFont("Helvetica", font_size_small)
            exported_text = f"Exported: {exported_formatted}"
            pdf_canvas.drawString(logo_end_x, info_y, exported_text)
            
            # Update y_position to start content below header
            y_position = header_top - 0.3 * inch
        
        def draw_footer() -> None:
            """Draw footer with page number."""
            pdf_canvas.setFillColor(text_secondary)
            pdf_canvas.setFont("Helvetica", 8)
            footer_text = f"Generated by AIQ Backend • Page {page_num[0]}"
            pdf_canvas.drawString(margin_left, margin_bottom - 0.2 * inch, footer_text)
            
            # Footer line
            pdf_canvas.setStrokeColor(assistant_bubble_border)
            pdf_canvas.setLineWidth(0.5)
            pdf_canvas.line(margin_left, margin_bottom - 0.1 * inch, page_width - margin_right, margin_bottom - 0.1 * inch)
        
        def draw_message_bubble(role: str, content: str, timestamp: str = "") -> None:
            """Draw a beautiful modern message bubble."""
            nonlocal y_position
            
            is_user = role.upper() == "USER"
            bubble_width = content_width * 0.7  # 70% of content width for better readability
            bubble_x = margin_left if not is_user else (page_width - margin_right - bubble_width)
            
            # Clean content
            cleaned_text = clean_content(content)
            
            # Wrap text more accurately
            chars_per_line = int(bubble_width / 5.5)  # Better character estimation
            wrapped_lines = wrap(cleaned_text, width=chars_per_line)
            
            # Calculate bubble height with better spacing
            line_height = 0.22 * inch
            padding_vertical = 0.2 * inch
            padding_horizontal = 0.25 * inch
            header_height = 0.35 * inch
            bubble_height = len(wrapped_lines) * line_height + padding_vertical * 2 + header_height
            
            # Check if we need a new page
            if y_position - bubble_height < margin_bottom + 0.5 * inch:
                # Draw footer on current page before creating new one
                draw_footer()
                pdf_canvas.showPage()
                page_num[0] += 1
                y_position = page_height - margin_top
            
            # Draw bubble shadow (subtle depth)
            shadow_offset = 0.02 * inch
            pdf_canvas.setFillColor(colors.HexColor("#e2e8f0" if not is_user else "#1e3a8a"))
            pdf_canvas.roundRect(
                bubble_x + shadow_offset,
                y_position - bubble_height - shadow_offset,
                bubble_width,
                bubble_height,
                12,
                fill=1,
                stroke=0
            )
            
            # Draw bubble background with gradient effect
            if is_user:
                # User bubble: vibrant blue with subtle gradient
                pdf_canvas.setFillColor(user_bubble_color)
            else:
                # Assistant bubble: light gray
                pdf_canvas.setFillColor(assistant_bubble_color)
            
            pdf_canvas.setStrokeColor(user_bubble_dark if is_user else assistant_bubble_border)
            pdf_canvas.setLineWidth(1)
            pdf_canvas.roundRect(
                bubble_x,
                y_position - bubble_height,
                bubble_width,
                bubble_height,
                12,
                fill=1,
                stroke=1
            )
            
            # Draw role label and timestamp in header area
            label_y = y_position - 0.25 * inch
            pdf_canvas.setFont("Helvetica-Bold", 10)
            
            if is_user:
                pdf_canvas.setFillColor(white)
            else:
                pdf_canvas.setFillColor(primary_color)
            
            role_text = role.upper()
            pdf_canvas.drawString(bubble_x + padding_horizontal, label_y, role_text)
            
            # Timestamp
            if timestamp:
                formatted_time = format_timestamp(timestamp)
                if formatted_time:
                    pdf_canvas.setFont("Helvetica", 8)
                    if is_user:
                        pdf_canvas.setFillColor(colors.HexColor("#bfdbfe"))
                    else:
                        pdf_canvas.setFillColor(text_secondary)
                    timestamp_x = bubble_x + padding_horizontal + pdf_canvas.stringWidth(role_text, "Helvetica-Bold", 10) + 0.15 * inch
                    pdf_canvas.drawString(timestamp_x, label_y, f"• {formatted_time}")
            
            # Draw message content with better typography
            text_y = y_position - header_height - padding_vertical
            pdf_canvas.setFont("Helvetica", 10)
            
            if is_user:
                pdf_canvas.setFillColor(white)
            else:
                pdf_canvas.setFillColor(text_color)
            
            # Draw wrapped text lines
            for line in wrapped_lines:
                # Handle very long words that don't fit
                if pdf_canvas.stringWidth(line, "Helvetica", 10) > bubble_width - padding_horizontal * 2:
                    # Try to break it further
                    words = line.split()
                    current_line = ""
                    for word in words:
                        test_line = current_line + (" " if current_line else "") + word
                        if pdf_canvas.stringWidth(test_line, "Helvetica", 10) <= bubble_width - padding_horizontal * 2:
                            current_line = test_line
                        else:
                            if current_line:
                                pdf_canvas.drawString(bubble_x + padding_horizontal, text_y, current_line)
                                text_y -= line_height
                            current_line = word
                    if current_line:
                        pdf_canvas.drawString(bubble_x + padding_horizontal, text_y, current_line)
                        text_y -= line_height
                else:
                    pdf_canvas.drawString(bubble_x + padding_horizontal, text_y, line)
                text_y -= line_height
            
            y_position -= bubble_height + 0.4 * inch  # More spacing between messages
        
        # Draw header on first page
        draw_header(is_first_page=True)
        
        # Draw footer on first page
        draw_footer()
        
        # Draw messages
        if not export_messages:
            pdf_canvas.setFillColor(text_color)
            pdf_canvas.setFont("Helvetica", 14)
            pdf_canvas.drawString(margin_left, y_position, "No messages available for this session.")
        else:
            for message in export_messages:
                role = message.get("type") or "ASSISTANT"
                content = message.get("content") or ""
                timestamp = message.get("timestamp") or ""
                draw_message_bubble(role, content, timestamp)
        
        # Add footer on last page (in case it wasn't drawn yet)
        draw_footer()
        
        pdf_canvas.save()
        buffer.seek(0)
        file_bytes = buffer.getvalue()

    if is_share:
        visibility_value = share_visibility.lower()
        if visibility_value not in {"public", "private"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="share_visibility must be 'public' or 'private'",
            )

        try:
            blob_service = BlobStorageService(container_name=settings.azure_storage_container_name)
        except ValueError as exc:
            logger.error(
                "Blob storage not configured for chat export share",
                extra={"session_id": session.session_id, "user_id": str(current_user.id)},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="File storage service is not configured",
            ) from exc

        try:
            _, blob_name, uploaded_size = await blob_service.upload_attachment_file(
                file_name=file_name,
                file_content=file_bytes,
                content_type=content_type,
                user_id=str(current_user.id),
                entity_type="chat_session_export",
                entity_id=session.session_id,
            )
            # Generate share link with 30 minutes expiry
            access_url = blob_service.generate_blob_access_url(
                blob_name=blob_name,
                visibility=visibility_value,
                expiry_hours=0.5,  # 30 minutes expiry for chat share links
            )
        except AzureError as exc:
            logger.error(
                "Failed to upload chat export to blob storage",
                extra={
                    "session_id": session.session_id,
                    "user_id": str(current_user.id),
                    "file_name": file_name,
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to store export file",
            ) from exc
        except Exception as exc:
            logger.error(
                "Unexpected error while uploading chat export",
                extra={
                    "session_id": session.session_id,
                    "user_id": str(current_user.id),
                    "file_name": file_name,
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unexpected error while storing export file",
            ) from exc

        try:
            attachment = Attachment(
                user_id=current_user.id,
                file_name=file_name,
                file_size=uploaded_size,
                content_type=content_type,
                blob_url=access_url,
                blob_name=blob_name,
                entity_type="chat_session_export",
                entity_id=session.session_id,
                is_active=True,
            )
            db.add(attachment)
            await db.flush()
            await db.refresh(attachment)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.error(
                "Failed to persist chat export attachment metadata",
                extra={
                    "session_id": session.session_id,
                    "user_id": str(current_user.id),
                    "file_name": file_name,
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to record export attachment",
            ) from exc

        # Log conversation share
        correlation_id = get_correlation_id()
        log_conversation_share(
            chat_id=str(session.id),
            session_id=session.session_id,
            user_id=str(current_user.id),
            username=current_user.username or current_user.email,
            share_url=attachment.blob_url if visibility_value == "public" else None,
            visibility=visibility_value,
            format=response_format,
            correlation_id=correlation_id,
            email=current_user.email,
            role="ADMIN" if current_user.is_admin else "NORMAL",
            department=current_user.azure_department,
            user_entra_id=current_user.azure_ad_id,
        )

        return {
            "success": True,
            "attachment_id": str(attachment.id),
            "file_name": attachment.file_name,
            "file_size": attachment.file_size,
            "content_type": attachment.content_type,
            "blob_url": attachment.blob_url if visibility_value == "public" else None,
            "visibility": visibility_value,
            "share_url": attachment.blob_url if visibility_value == "public" else None,
            "entity_type": attachment.entity_type,
            "entity_id": attachment.entity_id,
            "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
        }

    # Log conversation download
    correlation_id = get_correlation_id()
    log_conversation_download(
        chat_id=str(session.id),
        session_id=session.session_id,
        user_id=str(current_user.id),
        username=current_user.username or current_user.email,
        format=response_format,
        correlation_id=correlation_id,
        email=current_user.email,
        role="ADMIN" if current_user.is_admin else "NORMAL",
        department=current_user.azure_department,
        user_entra_id=current_user.azure_ad_id,
    )

    quoted_filename = quote(file_name)
    content_disposition = f'attachment; filename="{file_name}"'
    if quoted_filename != file_name:
        content_disposition += f"; filename*=UTF-8''{quoted_filename}"
    headers = {
        "Content-Disposition": content_disposition,
        "Content-Length": str(len(file_bytes)),
    }

    return Response(content=file_bytes, media_type=content_type, headers=headers)

