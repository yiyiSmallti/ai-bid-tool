use fontdue::{Font, FontSettings};
use image::codecs::jpeg::JpegDecoder;
use image::codecs::png::{CompressionType, FilterType, PngDecoder, PngEncoder};
use image::imageops;
use image::{DynamicImage, ImageDecoder, ImageEncoder, Limits, Rgb, RgbImage};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};
use std::io::{self, Cursor, Read, Write};

const MAX_IMAGE_BYTES: usize = 40 * 1024 * 1024;
const MAX_REQUEST_BYTES: usize = 64 * 1024;
const MAX_DIMENSION: u32 = 8192;
const MAX_PIXELS: u64 = 20_000_000;
const MAX_DECODE_BYTES: u64 = MAX_PIXELS * 8;
const MAX_REDACTIONS: usize = 200;
const MAX_BOXES: usize = 20;
const MARKUP_MIN_WIDTH: u32 = 1024;
const FOOTER_MARGIN: u32 = 16;
const FOOTER_FONT_SIZE: f32 = 16.0;
const FOOTER_LINE_HEIGHT: u32 = 24;
const FONT_BYTES: &[u8] = include_bytes!("../assets/NotoSansSC-Renderer.ttf");

#[derive(Debug)]
struct RendererError {
    code: &'static str,
    message: &'static str,
}

impl RendererError {
    fn invalid(message: &'static str) -> Self {
        Self {
            code: "invalid_request",
            message,
        }
    }

    fn image(message: &'static str) -> Self {
        Self {
            code: "invalid_image",
            message,
        }
    }

    fn internal(message: &'static str) -> Self {
        Self {
            code: "renderer_failure",
            message,
        }
    }
}

impl Display for RendererError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.message)
    }
}

impl std::error::Error for RendererError {}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct PixelRect {
    x: u32,
    y: u32,
    width: u32,
    height: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RenderPlan {
    #[serde(default)]
    redact: Vec<PixelRect>,
    #[serde(default)]
    crop: Option<PixelRect>,
    #[serde(default)]
    boxes: Vec<PixelRect>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RenderRequest {
    plan: RenderPlan,
    profile: String,
    provenance: Provenance,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Provenance {
    source_kind: Option<String>,
    source_sha256: Option<String>,
    plan_sha256: Option<String>,
    source_time_kind: Option<String>,
    source_time: Option<String>,
    source_id: Option<String>,
    page: Option<u32>,
}

#[derive(Debug, Serialize)]
struct ImageDescriptor {
    sha256: String,
    size_bytes: usize,
    width_px: u32,
    height_px: u32,
    media_type: &'static str,
}

#[derive(Debug, Serialize)]
struct PixelMapping {
    crop: PixelRect,
    content_offset_x: u32,
    content_offset_y: u32,
    content_width: u32,
    content_height: u32,
    footer_height: u32,
}

#[derive(Debug, Serialize)]
struct Rendering {
    image: ImageDescriptor,
    mapping: PixelMapping,
    source_width: u32,
    source_height: u32,
    plan_sha256: String,
    profile: String,
}

fn main() {
    if let Err(error) = run() {
        let diagnostic = serde_json::json!({"code": error.code, "message": error.message});
        let _ = writeln!(io::stderr().lock(), "{diagnostic}");
        std::process::exit(2);
    }
}

fn run() -> Result<(), RendererError> {
    let mut stdin = io::stdin().lock();
    let mut size_bytes = [0_u8; 8];
    stdin
        .read_exact(&mut size_bytes)
        .map_err(|_| RendererError::invalid("request frame is incomplete"))?;
    let request_size = u64::from_be_bytes(size_bytes);
    if request_size < 2 || request_size > MAX_REQUEST_BYTES as u64 {
        return Err(RendererError::invalid("request metadata exceeds its limit"));
    }
    let mut request_bytes = vec![0_u8; request_size as usize];
    stdin
        .read_exact(&mut request_bytes)
        .map_err(|_| RendererError::invalid("request metadata is incomplete"))?;
    let request: RenderRequest = serde_json::from_slice(&request_bytes)
        .map_err(|_| RendererError::invalid("request metadata is invalid"))?;
    let mut content = Vec::new();
    stdin
        .take((MAX_IMAGE_BYTES + 1) as u64)
        .read_to_end(&mut content)
        .map_err(|_| RendererError::image("image input could not be read"))?;
    if content.is_empty() || content.len() > MAX_IMAGE_BYTES {
        return Err(RendererError::image("image input exceeds its limit"));
    }

    let (png, rendering) = render(content, request)?;
    let metadata = serde_json::to_vec(&rendering)
        .map_err(|_| RendererError::internal("rendering receipt could not be encoded"))?;
    if metadata.len() > MAX_REQUEST_BYTES {
        return Err(RendererError::internal(
            "rendering receipt exceeds its limit",
        ));
    }
    let mut stdout = io::stdout().lock();
    stdout
        .write_all(&(metadata.len() as u64).to_be_bytes())
        .and_then(|_| stdout.write_all(&metadata))
        .and_then(|_| stdout.write_all(&png))
        .and_then(|_| stdout.flush())
        .map_err(|_| RendererError::internal("rendered output could not be written"))
}

fn render(content: Vec<u8>, request: RenderRequest) -> Result<(Vec<u8>, Rendering), RendererError> {
    validate_profile(&request.profile)?;
    validate_plan_shape(&request.plan)?;
    let mut source = decode_oriented(&content)?;
    let (source_width, source_height) = source.dimensions();
    validate_dimensions(source_width, source_height)?;
    validate_plan_bounds(&request.plan, source_width, source_height)?;
    validate_provenance(&request)?;
    let plan_sha256 = plan_sha256(&request.plan)?;

    for rectangle in &request.plan.redact {
        fill_rectangle(&mut source, rectangle, Rgb([0, 0, 0]));
    }
    let crop = request.plan.crop.clone().unwrap_or(PixelRect {
        x: 0,
        y: 0,
        width: source_width,
        height: source_height,
    });
    let mut content_image =
        imageops::crop_imm(&source, crop.x, crop.y, crop.width, crop.height).to_image();
    for rectangle in &request.plan.boxes {
        let translated = PixelRect {
            x: rectangle.x - crop.x,
            y: rectangle.y - crop.y,
            width: rectangle.width,
            height: rectangle.height,
        };
        draw_inner_border(&mut content_image, &translated);
    }

    let (output_image, content_offset_x, footer_height) =
        if request.profile == "screenshot-markup-v1" {
            add_markup_footer(content_image, &request.provenance)?
        } else {
            (content_image, 0, 0)
        };
    validate_dimensions(output_image.width(), output_image.height())?;
    let png = encode_png(&output_image)?;
    if png.len() > MAX_IMAGE_BYTES {
        return Err(RendererError::image("rendered PNG exceeds 40 MiB"));
    }
    let rendering = Rendering {
        image: ImageDescriptor {
            sha256: hex_sha256(&png),
            size_bytes: png.len(),
            width_px: output_image.width(),
            height_px: output_image.height(),
            media_type: "image/png",
        },
        mapping: PixelMapping {
            crop: crop.clone(),
            content_offset_x,
            content_offset_y: 0,
            content_width: crop.width,
            content_height: crop.height,
            footer_height,
        },
        source_width,
        source_height,
        plan_sha256,
        profile: request.profile,
    };
    Ok((png, rendering))
}

fn validate_profile(profile: &str) -> Result<(), RendererError> {
    match profile {
        "screenshot-privacy-v1" | "screenshot-markup-v1" | "prototype-clean-v1" => Ok(()),
        _ => Err(RendererError::invalid("rendering profile is unsupported")),
    }
}

fn validate_plan_shape(plan: &RenderPlan) -> Result<(), RendererError> {
    if plan.redact.len() > MAX_REDACTIONS {
        return Err(RendererError::invalid("too many redaction rectangles"));
    }
    if plan.boxes.len() > MAX_BOXES {
        return Err(RendererError::invalid("too many box rectangles"));
    }
    for rectangle in plan
        .redact
        .iter()
        .chain(plan.crop.iter())
        .chain(plan.boxes.iter())
    {
        if rectangle.width == 0
            || rectangle.height == 0
            || rectangle.x > MAX_DIMENSION
            || rectangle.y > MAX_DIMENSION
            || rectangle.width > MAX_DIMENSION
            || rectangle.height > MAX_DIMENSION
            || rectangle.x.saturating_add(rectangle.width) > MAX_DIMENSION
            || rectangle.y.saturating_add(rectangle.height) > MAX_DIMENSION
        {
            return Err(RendererError::invalid("rectangle is invalid"));
        }
    }
    if let Some(crop) = &plan.crop {
        if plan
            .boxes
            .iter()
            .any(|rectangle| !contains(crop, rectangle))
        {
            return Err(RendererError::invalid(
                "box rectangle is outside the crop rectangle",
            ));
        }
    }
    Ok(())
}

fn validate_plan_bounds(plan: &RenderPlan, width: u32, height: u32) -> Result<(), RendererError> {
    let source = PixelRect {
        x: 0,
        y: 0,
        width,
        height,
    };
    if plan
        .redact
        .iter()
        .chain(plan.crop.iter())
        .chain(plan.boxes.iter())
        .any(|rectangle| !contains(&source, rectangle))
    {
        return Err(RendererError::invalid(
            "rectangle is outside the oriented source image",
        ));
    }
    Ok(())
}

fn contains(container: &PixelRect, item: &PixelRect) -> bool {
    item.x >= container.x
        && item.y >= container.y
        && item.x.saturating_add(item.width) <= container.x.saturating_add(container.width)
        && item.y.saturating_add(item.height) <= container.y.saturating_add(container.height)
}

fn validate_provenance(request: &RenderRequest) -> Result<(), RendererError> {
    if request.profile == "screenshot-markup-v1" {
        for key in [
            request.provenance.source_kind.as_deref(),
            request.provenance.source_sha256.as_deref(),
            request.provenance.plan_sha256.as_deref(),
            request.provenance.source_time_kind.as_deref(),
        ] {
            let value =
                key.ok_or_else(|| RendererError::invalid("markup provenance is incomplete"))?;
            if value.is_empty()
                || value.len() > 128
                || value.chars().any(|character| character.is_control())
                || !value
                    .chars()
                    .all(|character| character.is_ascii_graphic() || character == ' ')
            {
                return Err(RendererError::invalid("markup provenance is invalid"));
            }
        }
        for optional_value in [
            request.provenance.source_time.as_deref(),
            request.provenance.source_id.as_deref(),
        ]
        .into_iter()
        .flatten()
        {
            if optional_value.is_empty()
                || optional_value.len() > 128
                || optional_value
                    .chars()
                    .any(|character| character.is_control())
                || !optional_value
                    .chars()
                    .all(|character| character.is_ascii_graphic() || character == ' ')
            {
                return Err(RendererError::invalid("markup provenance is invalid"));
            }
        }
        let declared_sha256 = request
            .provenance
            .source_sha256
            .as_deref()
            .ok_or_else(|| RendererError::invalid("markup provenance is incomplete"))?;
        if declared_sha256.len() != 64
            || !declared_sha256
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(RendererError::invalid("source hash is invalid"));
        }
        let provenance_plan_sha256 = request
            .provenance
            .plan_sha256
            .as_deref()
            .ok_or_else(|| RendererError::invalid("markup provenance is incomplete"))?;
        if provenance_plan_sha256.len() != 64
            || !provenance_plan_sha256
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(RendererError::invalid("provenance plan hash is invalid"));
        }
        if request
            .provenance
            .source_kind
            .as_deref()
            .is_some_and(|kind| kind.contains("certificate"))
            && request.provenance.page.is_none()
        {
            return Err(RendererError::invalid(
                "certificate provenance page is required",
            ));
        }
        if request.provenance.page == Some(0) {
            return Err(RendererError::invalid("provenance page is invalid"));
        }
    }
    Ok(())
}

fn plan_sha256(plan: &RenderPlan) -> Result<String, RendererError> {
    let mut normalized = BTreeMap::new();
    normalized.insert(
        "redact",
        serde_json::to_value(&plan.redact)
            .map_err(|_| RendererError::internal("plan could not be normalized"))?,
    );
    normalized.insert(
        "crop",
        serde_json::to_value(&plan.crop)
            .map_err(|_| RendererError::internal("plan could not be normalized"))?,
    );
    normalized.insert(
        "boxes",
        serde_json::to_value(&plan.boxes)
            .map_err(|_| RendererError::internal("plan could not be normalized"))?,
    );
    let encoded = serde_json::to_vec(&normalized)
        .map_err(|_| RendererError::internal("plan could not be normalized"))?;
    Ok(hex_sha256(&encoded))
}

fn decode_oriented(content: &[u8]) -> Result<RgbImage, RendererError> {
    if content.starts_with(b"\x89PNG\r\n\x1a\n") {
        if png_is_animated(content)? {
            return Err(RendererError::image("animated PNG input is unsupported"));
        }
        let decoder = PngDecoder::with_limits(Cursor::new(content), decoder_limits())
            .map_err(|_| RendererError::image("PNG input could not be decoded"))?;
        decode_from(decoder)
    } else if content.starts_with(b"\xff\xd8") {
        let mut decoder = JpegDecoder::new(Cursor::new(content))
            .map_err(|_| RendererError::image("JPEG input could not be decoded"))?;
        decoder
            .set_limits(decoder_limits())
            .map_err(|_| RendererError::image("JPEG decode limits could not be applied"))?;
        decode_from(decoder)
    } else {
        Err(RendererError::image("input must be PNG or JPEG"))
    }
}

fn decode_from<D: ImageDecoder>(mut decoder: D) -> Result<RgbImage, RendererError> {
    let (width, height) = decoder.dimensions();
    validate_dimensions(width, height)?;
    if decoder.total_bytes() > MAX_DECODE_BYTES {
        return Err(RendererError::image(
            "decoded image bytes exceed the fixed limit",
        ));
    }
    let orientation = decoder
        .orientation()
        .map_err(|_| RendererError::image("image orientation is invalid"))?;
    let mut image = DynamicImage::from_decoder(decoder)
        .map_err(|_| RendererError::image("image pixels could not be decoded"))?;
    image.apply_orientation(orientation);
    // Composite alpha onto opaque white instead of exposing RGB values hidden
    // behind transparent PNG pixels.
    let rgba = image.to_rgba8();
    let rgb = RgbImage::from_fn(rgba.width(), rgba.height(), |x, y| {
        let pixel = rgba.get_pixel(x, y).0;
        let alpha = u16::from(pixel[3]);
        let blend =
            |channel: u8| ((u16::from(channel) * alpha + 255 * (255 - alpha) + 127) / 255) as u8;
        Rgb([blend(pixel[0]), blend(pixel[1]), blend(pixel[2])])
    });
    validate_dimensions(rgb.width(), rgb.height())?;
    Ok(rgb)
}

fn decoder_limits() -> Limits {
    let mut limits = Limits::default();
    limits.max_image_width = Some(MAX_DIMENSION);
    limits.max_image_height = Some(MAX_DIMENSION);
    limits.max_alloc = Some(MAX_DECODE_BYTES);
    limits
}

fn png_is_animated(content: &[u8]) -> Result<bool, RendererError> {
    let mut offset = 8_usize;
    while offset < content.len() {
        if content.len() - offset < 12 {
            return Err(RendererError::image("PNG chunk is truncated"));
        }
        let length = u32::from_be_bytes(
            content[offset..offset + 4]
                .try_into()
                .map_err(|_| RendererError::image("PNG chunk is invalid"))?,
        ) as usize;
        let end = offset
            .checked_add(12)
            .and_then(|value| value.checked_add(length))
            .ok_or_else(|| RendererError::image("PNG chunk length is invalid"))?;
        if end > content.len() {
            return Err(RendererError::image("PNG chunk is truncated"));
        }
        if &content[offset + 4..offset + 8] == b"acTL" {
            return Ok(true);
        }
        offset = end;
    }
    Ok(false)
}

fn validate_dimensions(width: u32, height: u32) -> Result<(), RendererError> {
    if width == 0
        || height == 0
        || width > MAX_DIMENSION
        || height > MAX_DIMENSION
        || u64::from(width) * u64::from(height) > MAX_PIXELS
    {
        return Err(RendererError::image(
            "image dimensions exceed the fixed limit",
        ));
    }
    Ok(())
}

fn fill_rectangle(image: &mut RgbImage, rectangle: &PixelRect, color: Rgb<u8>) {
    for y in rectangle.y..rectangle.y + rectangle.height {
        for x in rectangle.x..rectangle.x + rectangle.width {
            image.put_pixel(x, y, color);
        }
    }
}

fn draw_inner_border(image: &mut RgbImage, rectangle: &PixelRect) {
    let color = Rgb([255, 0, 0]);
    for relative_y in 0..rectangle.height {
        for relative_x in 0..rectangle.width {
            let on_border = relative_x < 2
                || relative_y < 2
                || relative_x >= rectangle.width.saturating_sub(2)
                || relative_y >= rectangle.height.saturating_sub(2);
            if on_border {
                image.put_pixel(rectangle.x + relative_x, rectangle.y + relative_y, color);
            }
        }
    }
}

fn add_markup_footer(
    content: RgbImage,
    provenance: &Provenance,
) -> Result<(RgbImage, u32, u32), RendererError> {
    let source_kind = provenance
        .source_kind
        .as_deref()
        .ok_or_else(|| RendererError::invalid("markup provenance is incomplete"))?;
    let source_sha256 = provenance
        .source_sha256
        .as_deref()
        .ok_or_else(|| RendererError::invalid("markup provenance is incomplete"))?;
    let provenance_plan_sha256 = provenance
        .plan_sha256
        .as_deref()
        .ok_or_else(|| RendererError::invalid("markup provenance is incomplete"))?;
    let source_time_kind = provenance
        .source_time_kind
        .as_deref()
        .ok_or_else(|| RendererError::invalid("markup provenance is incomplete"))?;
    let source_time = provenance.source_time.as_deref().unwrap_or("UNKNOWN");
    let source_id = provenance.source_id.as_deref().unwrap_or("UNAVAILABLE");
    let page = provenance
        .page
        .map(|value| format!(" | PAGE={value}"))
        .unwrap_or_default();
    let text = format!(
        "已脱敏 | SOURCE_KIND={source_kind} | SOURCE_ID={source_id}{page} | SOURCE_SHA256={source_sha256} | SOURCE_TIME_KIND={source_time_kind} | SOURCE_TIME={source_time} | PLAN_SHA256={provenance_plan_sha256}"
    );
    let font = Font::from_bytes(FONT_BYTES, FontSettings::default())
        .map_err(|_| RendererError::internal("fixed footer font could not be loaded"))?;
    let canvas_width = content.width().max(MARKUP_MIN_WIDTH);
    let available_width = canvas_width
        .checked_sub(FOOTER_MARGIN * 2)
        .ok_or_else(|| RendererError::image("footer width is invalid"))?;
    let lines = wrap_text(&font, &text, available_width)?;
    let footer_height = FOOTER_MARGIN
        .checked_mul(2)
        .and_then(|value| value.checked_add(FOOTER_LINE_HEIGHT * lines.len() as u32))
        .ok_or_else(|| RendererError::image("footer height exceeds its limit"))?;
    let canvas_height = content
        .height()
        .checked_add(footer_height)
        .ok_or_else(|| RendererError::image("rendered image height exceeds its limit"))?;
    validate_dimensions(canvas_width, canvas_height)?;
    let mut canvas = RgbImage::from_pixel(canvas_width, canvas_height, Rgb([255, 255, 255]));
    let content_offset_x = (canvas_width - content.width()) / 2;
    imageops::replace(&mut canvas, &content, i64::from(content_offset_x), 0);
    for (line_index, line) in lines.iter().enumerate() {
        let y = content.height() + FOOTER_MARGIN + line_index as u32 * FOOTER_LINE_HEIGHT;
        draw_text_line(&mut canvas, &font, line, FOOTER_MARGIN, y);
    }
    Ok((canvas, content_offset_x, footer_height))
}

fn wrap_text(font: &Font, text: &str, available_width: u32) -> Result<Vec<String>, RendererError> {
    let mut lines = vec![String::new()];
    let mut line_width = 0.0_f32;
    for character in text.chars() {
        let (metrics, _) = font.rasterize(character, FOOTER_FONT_SIZE);
        let advance = metrics.advance_width.max(1.0);
        if line_width + advance > available_width as f32 && !lines.last().unwrap().is_empty() {
            lines.push(String::new());
            line_width = 0.0;
        }
        if advance > available_width as f32 {
            return Err(RendererError::image(
                "footer glyph exceeds the canvas width",
            ));
        }
        lines.last_mut().unwrap().push(character);
        line_width += advance;
    }
    Ok(lines)
}

fn draw_text_line(image: &mut RgbImage, font: &Font, text: &str, start_x: u32, y: u32) {
    let mut cursor_x = start_x as f32;
    for character in text.chars() {
        let (metrics, bitmap) = font.rasterize(character, FOOTER_FONT_SIZE);
        let glyph_y = y + (FOOTER_LINE_HEIGHT.saturating_sub(metrics.height as u32)) / 2;
        for bitmap_y in 0..metrics.height {
            for bitmap_x in 0..metrics.width {
                let alpha = bitmap[bitmap_y * metrics.width + bitmap_x];
                if alpha == 0 {
                    continue;
                }
                let target_x = cursor_x as u32 + bitmap_x as u32;
                let target_y = glyph_y + bitmap_y as u32;
                if target_x < image.width() && target_y < image.height() {
                    let value = 255_u8.saturating_sub(alpha);
                    image.put_pixel(target_x, target_y, Rgb([value, value, value]));
                }
            }
        }
        cursor_x += metrics.advance_width.max(1.0);
    }
}

fn encode_png(image: &RgbImage) -> Result<Vec<u8>, RendererError> {
    let mut output = Vec::new();
    PngEncoder::new_with_quality(&mut output, CompressionType::Best, FilterType::Adaptive)
        .write_image(
            image.as_raw(),
            image.width(),
            image.height(),
            image::ExtendedColorType::Rgb8,
        )
        .map_err(|_| RendererError::image("rendered PNG could not be encoded"))?;
    Ok(output)
}

fn hex_sha256(content: &[u8]) -> String {
    let digest = Sha256::digest(content);
    let mut output = String::with_capacity(64);
    for byte in digest {
        use std::fmt::Write as _;
        let _ = write!(&mut output, "{byte:02x}");
    }
    output
}
