use super::*;
use serde_json::{json, Value};

const PROTOCOL: &str = "annotation-render-v1";
const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Plan {
    pub crop: Option<PixelRect>,
    pub boxes: Vec<PixelRect>,
}

#[derive(Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct Identity {
    protocol_version: String,
    profile: String,
    version: String,
    binary_sha256: String,
    font_bundle_sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    protocol: String,
    source: Value,
    plan: Plan,
    plan_sha256: String,
    renderer: Identity,
    content_mode: String,
    input_content_sha256: String,
    provenance_sha256: String,
    approval: Option<Value>,
}

fn current_identity(profile: &str) -> Result<Identity, RendererError> {
    let binary = std::env::current_exe()
        .and_then(std::fs::read)
        .map_err(|_| RendererError::internal("renderer build could not be identified"))?;
    Ok(Identity {
        protocol_version: PROTOCOL.into(),
        profile: profile.into(),
        version: VERSION.into(),
        binary_sha256: hex_sha256(&binary),
        font_bundle_sha256: hex_sha256(FONT_BYTES),
    })
}

pub(super) fn canonical_sha256(value: &Value) -> Result<String, RendererError> {
    // All hashed B05 projections contain integers and ASCII identity/hash values.
    let encoded = serde_json::to_vec(value)
        .map_err(|_| RendererError::invalid("canonical projection is invalid"))?;
    if !encoded.is_ascii() {
        return Err(RendererError::invalid("canonical projection must be ASCII"));
    }
    Ok(hex_sha256(&encoded))
}

fn keys(value: &Value, expected: &str) -> Result<(), RendererError> {
    let object = value
        .as_object()
        .ok_or_else(|| RendererError::invalid("object required"))?;
    let names: Vec<_> = expected.split_whitespace().collect();
    if object.len() != names.len() || names.iter().any(|name| !object.contains_key(*name)) {
        return Err(RendererError::invalid(
            "object fields differ from the fixed contract",
        ));
    }
    Ok(())
}

fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str, RendererError> {
    let item = value[key]
        .as_str()
        .ok_or_else(|| RendererError::invalid("string field required"))?;
    if item.is_empty()
        || item.len() > 128
        || !item
            .bytes()
            .all(|byte| byte.is_ascii_graphic() || byte == b' ')
    {
        return Err(RendererError::invalid("identity field is invalid"));
    }
    Ok(item)
}

fn hash(value: &Value, key: &str) -> Result<(), RendererError> {
    let item = text(value, key)?;
    if item.len() != 64
        || !item
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(RendererError::invalid("hash field is invalid"));
    }
    Ok(())
}

fn uuid(value: &Value, key: &str) -> Result<(), RendererError> {
    let item = text(value, key)?;
    if item.len() != 36
        || item.bytes().enumerate().any(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b != b'-'
            } else {
                !b.is_ascii_digit() && !(b'a'..=b'f').contains(&b)
            }
        })
    {
        return Err(RendererError::invalid("UUID field is invalid"));
    }
    Ok(())
}

fn number(value: &Value, key: &str) -> Result<u32, RendererError> {
    value[key]
        .as_u64()
        .and_then(|v| u32::try_from(v).ok())
        .ok_or_else(|| RendererError::invalid("unsigned integer field required"))
}

fn rectangle(value: &Value) -> Result<PixelRect, RendererError> {
    let rect: PixelRect = serde_json::from_value(value.clone())
        .map_err(|_| RendererError::invalid("rectangle is invalid"))?;
    validate_plan_shape(&RenderPlan {
        redact: vec![],
        crop: Some(rect.clone()),
        boxes: vec![],
    })?;
    Ok(rect)
}

fn mapping(value: &Value) -> Result<(), RendererError> {
    keys(
        value,
        "crop content_offset_x content_offset_y content_width content_height footer_height",
    )?;
    let crop = rectangle(&value["crop"])?;
    for key in [
        "content_offset_x",
        "content_offset_y",
        "content_width",
        "content_height",
        "footer_height",
    ] {
        number(value, key)?;
    }
    if number(value, "content_width")? != crop.width
        || number(value, "content_height")? != crop.height
    {
        return Err(RendererError::invalid(
            "mapping must preserve unscaled source content",
        ));
    }
    Ok(())
}

fn descriptor(value: &Value, preview: bool) -> Result<(), RendererError> {
    keys(
        value,
        if preview {
            "name sha256 size_bytes media_type width_px height_px"
        } else {
            "sha256 size_bytes media_type width_px height_px"
        },
    )?;
    hash(value, "sha256")?;
    validate_dimensions(number(value, "width_px")?, number(value, "height_px")?)?;
    if text(value, "media_type")? != "image/png"
        || number(value, "size_bytes")? == 0
        || number(value, "size_bytes")? as usize > MAX_IMAGE_BYTES
    {
        return Err(RendererError::invalid("source PNG descriptor is invalid"));
    }
    Ok(())
}

fn validate_source(source: &Value) -> Result<(), RendererError> {
    let certificate = text(source, "kind")? == "certificate_page";
    keys(
        source,
        if certificate {
            "kind archive source_png page_kind source_time_kind source_time original_sha256 selection_id resource_revision_id authorized_content"
        } else {
            "kind org_id task_id extraction_job_id asset_id parent_rendition_id archive page source_time_kind source_time original_sha256 source_png root_source_png_sha256 selection_id resource_revision_id authorized_content source_crop_mapping source_profile privacy_review_id privacy_lineage_sha256 root_mapping_sha256"
        },
    )?;
    descriptor(&source["source_png"], false)?;
    hash(source, "original_sha256")?;
    for key in ["selection_id", "resource_revision_id"] {
        uuid(source, key)?;
    }
    let authorized = rectangle(&source["authorized_content"])?;
    let bounds = PixelRect {
        x: 0,
        y: 0,
        width: number(&source["source_png"], "width_px")?,
        height: number(&source["source_png"], "height_px")?,
    };
    if !contains(&bounds, &authorized) {
        return Err(RendererError::invalid(
            "authorized content exceeds source PNG",
        ));
    }
    let archive = &source["archive"];
    if certificate {
        keys(archive,"id org_id task_id task_certificate_id certificate_id certificate_revision_id certificate_file_id source_kind original page render_profile dpi preview rendered_at created_by active_selection status confirmed_by eligible_for_draft_export")?;
        for key in [
            "id",
            "org_id",
            "task_id",
            "task_certificate_id",
            "certificate_id",
            "certificate_revision_id",
            "certificate_file_id",
            "created_by",
        ] {
            uuid(archive, key)?;
        }
        keys(
            &archive["original"],
            "name sha256 size_bytes media_type page_count",
        )?;
        hash(&archive["original"], "sha256")?;
        descriptor(&archive["preview"], true)?;
        let mut preview = archive["preview"].clone();
        preview.as_object_mut().unwrap().remove("name");
        if source["source_png"] != preview
            || source["original_sha256"] != archive["original"]["sha256"]
            || source["selection_id"] != archive["task_certificate_id"]
            || source["resource_revision_id"] != archive["certificate_revision_id"]
            || source["source_time"] != archive["rendered_at"]
            || text(source, "source_time_kind")? != "server_page_rendered_at"
            || text(source, "page_kind")? != "pdf_page"
            || number(archive, "dpi")? != 150
            || number(archive, "page")? == 0
            || number(archive, "page")? > number(&archive["original"], "page_count")?
            || text(archive, "source_kind")? != "user_supplied_certificate_pdf"
            || text(archive, "render_profile")? != "pdf-page-preview-v1"
            || text(archive, "status")? != "unconfirmed_source"
            || archive["confirmed_by"] != Value::Null
            || archive["eligible_for_draft_export"] != false
            || authorized != bounds
        {
            return Err(RendererError::invalid(
                "certificate archive binding is invalid",
            ));
        }
    } else {
        if text(source, "kind")? != "vendor_rendition" {
            return Err(RendererError::invalid("source kind is unsupported"));
        }
        keys(archive,"id sandbox_run_id task_resource_id product_revision_id format source_field source_url_sha256 final_url_sha256 final_origin title captured_at content_sha256 archive policy_revision incomplete failed_request_count")?;
        keys(&archive["archive"], "sha256 size_bytes media_type")?;
        for key in [
            "org_id",
            "task_id",
            "extraction_job_id",
            "asset_id",
            "parent_rendition_id",
            "privacy_review_id",
        ] {
            uuid(source, key)?;
        }
        for key in [
            "root_source_png_sha256",
            "privacy_lineage_sha256",
            "root_mapping_sha256",
        ] {
            hash(source, key)?;
        }
        for key in [
            "id",
            "sandbox_run_id",
            "task_resource_id",
            "product_revision_id",
        ] {
            uuid(archive, key)?;
        }
        for key in ["source_url_sha256", "final_url_sha256", "content_sha256"] {
            hash(archive, key)?;
        }
        mapping(&source["source_crop_mapping"])?;
        keys(&source["page"], "kind page")?;
        let page = number(&source["page"], "page")?;
        let web = text(&source["page"], "kind")? == "web_page";
        let parent = &source["source_crop_mapping"];
        if page == 0
            || (web && page != 1)
            || (!web && text(&source["page"], "kind")? != "pdf_page")
            || web != (text(archive, "format")? == "web")
            || source["source_time"] != archive["captured_at"]
            || source["original_sha256"] != archive["content_sha256"]
            || source["selection_id"] != archive["task_resource_id"]
            || source["resource_revision_id"] != archive["product_revision_id"]
            || text(source, "source_time_kind")? != "vendor_captured_at"
            || text(source, "source_profile")? != "screenshot-markup-v1"
            || authorized.x != number(parent, "content_offset_x")?
            || authorized.y != number(parent, "content_offset_y")?
            || authorized.width != number(parent, "content_width")?
            || authorized.height != number(parent, "content_height")?
        {
            return Err(RendererError::invalid("vendor archive binding is invalid"));
        }
    }
    let timestamp = text(source, "source_time")?;
    if !timestamp.ends_with('Z') || timestamp.len() < 20 {
        return Err(RendererError::invalid("source time must be normalized UTC"));
    }
    Ok(())
}

fn validate_approval(value: &Value) -> Result<(), RendererError> {
    keys(value,"evidence_id card_id card_revision card_revision_id confirmed_by confirmed_at candidate_annotation_id candidate_rendition_id candidate_image_sha256 candidate_plan_sha256 candidate_content_mapping content_pixel_sha256 root_mapping_sha256 reviewed_region requirement decision_kind review_domain co_sign card_content_sha256 evidence_binding_sha256 approval_binding_sha256")?;
    for key in [
        "evidence_id",
        "card_id",
        "card_revision_id",
        "confirmed_by",
        "candidate_annotation_id",
        "candidate_rendition_id",
    ] {
        uuid(value, key)?;
    }
    for key in [
        "candidate_image_sha256",
        "candidate_plan_sha256",
        "content_pixel_sha256",
        "root_mapping_sha256",
        "card_content_sha256",
        "evidence_binding_sha256",
        "approval_binding_sha256",
    ] {
        hash(value, key)?;
    }
    if number(value, "card_revision")? == 0 {
        return Err(RendererError::invalid("approval revision is invalid"));
    }
    mapping(&value["candidate_content_mapping"])?;
    let region = rectangle(&value["reviewed_region"])?;
    let m = &value["candidate_content_mapping"];
    let plane = PixelRect {
        x: 0,
        y: 0,
        width: number(m, "content_width")?,
        height: number(m, "content_height")?,
    };
    if !contains(&plane, &region) {
        return Err(RendererError::invalid("approved region exceeds content"));
    }
    let req = &value["requirement"];
    keys(
        req,
        "requirement_id review_revision review_hash state source_binding_sha256",
    )?;
    uuid(req, "requirement_id")?;
    hash(req, "review_hash")?;
    hash(req, "source_binding_sha256")?;
    if text(req, "state")? != "confirmed" || number(req, "review_revision")? == 0 {
        return Err(RendererError::invalid("requirement approval is incomplete"));
    }
    let domain = text(value, "review_domain")?;
    if !["technical", "commercial"].contains(&domain) {
        return Err(RendererError::invalid("review domain is invalid"));
    }
    let cosign = &value["co_sign"];
    let single = text(value, "decision_kind")? == "single_domain";
    if !single && text(value, "decision_kind")? != "cosign" {
        return Err(RendererError::invalid("approval decision kind is invalid"));
    }
    if cosign.is_null() {
        if !single {
            return Err(RendererError::invalid("co-sign approval is incomplete"));
        }
    } else {
        keys(cosign,"policy_revision task_rule_revision round_id round_revision purpose required_domains status evidence_sha256 requirement_sha256 citation_sha256 content_sha256 signatures")?;
        number(cosign, "policy_revision")?;
        if number(cosign, "task_rule_revision")? == 0 {
            return Err(RendererError::invalid("task rule revision is invalid"));
        }
        let domains = cosign["required_domains"]
            .as_array()
            .ok_or_else(|| RendererError::invalid("co-sign domains are invalid"))?;
        let signatures = cosign["signatures"]
            .as_array()
            .ok_or_else(|| RendererError::invalid("co-sign signatures are invalid"))?;
        if domains.is_empty()
            || domains.len() > 2
            || !domains.iter().any(|v| v == domain)
            || domains
                .iter()
                .any(|v| ![json!("technical"), json!("commercial")].contains(v))
            || (domains.len() == 2 && domains[0] == domains[1])
            || (single && domains != &vec![json!(domain)])
        {
            return Err(RendererError::invalid("co-sign domains are invalid"));
        }
        if cosign["round_id"].is_null() {
            if !single
                || number(cosign, "round_revision")? != 0
                || text(cosign, "status")? != "not_required"
                || !cosign["purpose"].is_null()
                || !signatures.is_empty()
            {
                return Err(RendererError::invalid("historical approval is invalid"));
            }
        } else {
            uuid(cosign, "round_id")?;
            if number(cosign, "round_revision")? == 0
                || text(cosign, "status")? != "complete"
                || text(cosign, "purpose")? != "response"
                || signatures.len() != domains.len()
            {
                return Err(RendererError::invalid("co-sign approval is incomplete"));
            }
            for key in [
                "evidence_sha256",
                "requirement_sha256",
                "citation_sha256",
                "content_sha256",
            ] {
                hash(cosign, key)?;
            }
            let mut ids = std::collections::BTreeSet::new();
            let mut signers = std::collections::BTreeSet::new();
            let mut signed_domains = std::collections::BTreeSet::new();
            for signature in signatures {
                keys(
                    signature,
                    "id domain signer_user_id request_sha256 reason_sha256",
                )?;
                uuid(signature, "id")?;
                uuid(signature, "signer_user_id")?;
                hash(signature, "request_sha256")?;
                if !signature["reason_sha256"].is_null() {
                    hash(signature, "reason_sha256")?;
                }
                if !ids.insert(text(signature, "id")?)
                    || !signers.insert(text(signature, "signer_user_id")?)
                    || !signed_domains.insert(text(signature, "domain")?)
                    || !domains
                        .iter()
                        .any(|v| v == text(signature, "domain").unwrap_or(""))
                {
                    return Err(RendererError::invalid("co-sign identities are invalid"));
                }
            }
        }
    }
    let mut binding = value.clone();
    binding
        .as_object_mut()
        .unwrap()
        .remove("approval_binding_sha256");
    if canonical_sha256(&binding)? != text(value, "approval_binding_sha256")? {
        return Err(RendererError::invalid(
            "approval hash differs from normalized binding",
        ));
    }
    Ok(())
}

fn crop(request: &Request) -> Result<PixelRect, RendererError> {
    let authorized = rectangle(&request.source["authorized_content"])?;
    let crop = request.plan.crop.clone().unwrap_or(PixelRect {
        x: 0,
        y: 0,
        width: authorized.width,
        height: authorized.height,
    });
    let plane = PixelRect {
        x: 0,
        y: 0,
        width: authorized.width,
        height: authorized.height,
    };
    validate_plan_shape(&RenderPlan {
        redact: vec![],
        crop: Some(crop.clone()),
        boxes: request.plan.boxes.clone(),
    })?;
    if !contains(&plane, &crop) {
        return Err(RendererError::invalid("crop exceeds authorized content"));
    }
    Ok(crop)
}

fn root_mapping(request: &Request, crop: &PixelRect) -> Result<String, RendererError> {
    let s = &request.source;
    let vendor = text(s, "kind")? == "vendor_rendition";
    let parent = if vendor {
        rectangle(&s["source_crop_mapping"]["crop"])?
    } else {
        PixelRect {
            x: 0,
            y: 0,
            width: crop.width,
            height: crop.height,
        }
    };
    canonical_sha256(
        &json!({"source_kind":text(s,"kind")?,"root_source_png_sha256": if vendor {text(s,"root_source_png_sha256")?} else {text(&s["source_png"],"sha256")?},"source_root_mapping_sha256":if vendor {s["root_mapping_sha256"].clone()} else {Value::Null},"crop":{"x":parent.x+crop.x,"y":parent.y+crop.y,"width":crop.width,"height":crop.height}}),
    )
}

fn footer_pairs(request: &Request) -> Result<Vec<(String, String)>, RendererError> {
    let s = &request.source;
    let a = &s["archive"];
    let certificate = text(s, "kind")? == "certificate_page";
    let release = request.approval.is_some();
    let status = format!(
        "{} {}",
        if release { "CONFIRMED" } else { "UNCONFIRMED" },
        if certificate {
            "USER-SUPPLIED CERTIFICATE PAGE"
        } else {
            "ARCHIVED VENDOR PAGE"
        }
    );
    let mut pairs = vec![
        ("STATUS", status),
        (
            "SOURCE_KIND",
            if certificate {
                text(a, "source_kind")?
            } else {
                "archived_vendor_page"
            }
            .into(),
        ),
        (
            "SOURCE_ID",
            if certificate {
                text(a, "id")?
            } else {
                text(s, "asset_id")?
            }
            .into(),
        ),
        (
            "TASK_ID",
            if certificate {
                text(a, "task_id")?
            } else {
                text(s, "task_id")?
            }
            .into(),
        ),
        ("SELECTION_ID", text(s, "selection_id")?.into()),
        (
            "SOURCE_REVISION_ID",
            text(s, "resource_revision_id")?.into(),
        ),
        (
            if certificate {
                "SOURCE_FILE_ID"
            } else {
                "VENDOR_ARCHIVE_ID"
            },
            if certificate {
                text(a, "certificate_file_id")?
            } else {
                text(a, "id")?
            }
            .into(),
        ),
        (
            "PAGE_KIND",
            if certificate {
                text(s, "page_kind")?
            } else {
                text(&s["page"], "kind")?
            }
            .into(),
        ),
        (
            "PAGE",
            if certificate {
                number(a, "page")?
            } else {
                number(&s["page"], "page")?
            }
            .to_string(),
        ),
        (
            "DPI",
            if certificate { "150" } else { "NOT_APPLICABLE" }.into(),
        ),
        (
            "SOURCE_PROFILE",
            if certificate {
                text(a, "render_profile")?
            } else {
                text(s, "source_profile")?
            }
            .into(),
        ),
        ("SOURCE_TIME_KIND", text(s, "source_time_kind")?.into()),
        ("SOURCE_TIME", text(s, "source_time")?.into()),
        ("ORIGINAL_SHA256", text(s, "original_sha256")?.into()),
        (
            "SOURCE_PNG_SHA256",
            text(&s["source_png"], "sha256")?.into(),
        ),
        ("PLAN_SHA256", request.plan_sha256.clone()),
        ("RENDERER_PROFILE", request.renderer.profile.clone()),
        ("RENDERER_VERSION", request.renderer.version.clone()),
    ];
    if let Some(approval) = &request.approval {
        pairs.push((
            "APPROVAL_SHA256",
            text(approval, "approval_binding_sha256")?.into(),
        ));
    }
    Ok(pairs.into_iter().map(|(k, v)| (k.into(), v)).collect())
}

fn validate(request: &Request) -> Result<PixelRect, RendererError> {
    if request.protocol != PROTOCOL
        || !["annotation-candidate-v1", "annotation-release-v1"]
            .contains(&request.renderer.profile.as_str())
    {
        return Err(RendererError::invalid(
            "annotation protocol/profile is unsupported",
        ));
    }
    if request.renderer != current_identity(&request.renderer.profile)? {
        return Err(RendererError::invalid(
            "renderer identity does not match this binary/font build",
        ));
    }
    validate_source(&request.source)?;
    if canonical_sha256(
        &serde_json::to_value(&request.plan)
            .map_err(|_| RendererError::internal("plan encode failed"))?,
    )? != request.plan_sha256
    {
        return Err(RendererError::invalid(
            "plan hash differs from canonical plan",
        ));
    }
    let release = request.renderer.profile == "annotation-release-v1";
    if release != request.approval.is_some()
        || request.content_mode
            != if release {
                "marked_candidate_content"
            } else {
                "source_png"
            }
    {
        return Err(RendererError::invalid(
            "content mode does not match approval/profile",
        ));
    }
    let crop = crop(request)?;
    if let Some(approval) = &request.approval {
        validate_approval(approval)?;
        if approval["candidate_plan_sha256"] != request.plan_sha256
            || approval["root_mapping_sha256"] != root_mapping(request, &crop)?
            || approval["candidate_content_mapping"]["crop"] != serde_json::to_value(&crop).unwrap()
        {
            return Err(RendererError::invalid(
                "release mapping differs from approved candidate",
            ));
        }
    } else if request.input_content_sha256 != text(&request.source["source_png"], "sha256")? {
        return Err(RendererError::invalid(
            "candidate bytes must name the authorized source",
        ));
    }
    if canonical_sha256(&json!({"footer":footer_pairs(request)?,"renderer":request.renderer}))?
        != request.provenance_sha256
    {
        return Err(RendererError::invalid(
            "provenance hash differs from fixed footer projection",
        ));
    }
    Ok(crop)
}

fn layout(
    request: &Request,
    crop: &PixelRect,
) -> Result<(Value, Font, Vec<String>), RendererError> {
    let font = Font::from_bytes(FONT_BYTES, FontSettings::default())
        .map_err(|_| RendererError::internal("fixed footer font could not be loaded"))?;
    let width = crop.width.max(MARKUP_MIN_WIDTH);
    let text = footer_pairs(request)?
        .iter()
        .map(|(k, v)| format!("{k}={v}"))
        .collect::<Vec<_>>()
        .join(" | ");
    let lines = wrap_text(&font, &text, width - FOOTER_MARGIN * 2)?;
    let footer_height = FOOTER_MARGIN * 2 + FOOTER_LINE_HEIGHT * lines.len() as u32;
    let height = crop
        .height
        .checked_add(footer_height)
        .ok_or_else(|| RendererError::image("footer height overflow"))?;
    validate_dimensions(width, height)?;
    let mapping = json!({"crop":crop,"content_offset_x":(width-crop.width)/2,"content_offset_y":0,"content_width":crop.width,"content_height":crop.height,"footer_height":footer_height});
    if let Some(approval) = &request.approval {
        for key in [
            "crop",
            "content_offset_x",
            "content_offset_y",
            "content_width",
            "content_height",
        ] {
            if mapping[key] != approval["candidate_content_mapping"][key] {
                return Err(RendererError::invalid(
                    "release cannot reposition approved content",
                ));
            }
        }
    }
    Ok((
        json!({"width_px":width,"height_px":height,"maximum_png_bytes":MAX_IMAGE_BYTES,"mapping":mapping}),
        font,
        lines,
    ))
}

pub(super) fn pixel_sha256(image: &RgbImage) -> String {
    let mut bytes = Vec::with_capacity(26 + image.as_raw().len());
    bytes.extend_from_slice(b"annotation-rgb-v1\n");
    bytes.extend_from_slice(&image.width().to_be_bytes());
    bytes.extend_from_slice(&image.height().to_be_bytes());
    bytes.extend_from_slice(image.as_raw());
    hex_sha256(&bytes)
}

fn validate_input_png(content: &[u8]) -> Result<(), RendererError> {
    if content.len() < 33 || !content.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Err(RendererError::image(
            "annotation input must be a complete RGB PNG",
        ));
    }
    let mut offset = 8;
    let mut header = false;
    let mut pixels = false;
    let mut end = false;
    while offset < content.len() {
        if content.len() - offset < 12 {
            return Err(RendererError::image("annotation PNG chunk is truncated"));
        }
        let size = u32::from_be_bytes(content[offset..offset + 4].try_into().unwrap()) as usize;
        let next = offset
            .checked_add(size)
            .and_then(|n| n.checked_add(12))
            .filter(|n| *n <= content.len())
            .ok_or_else(|| RendererError::image("annotation PNG chunk exceeds input"))?;
        let kind = &content[offset + 4..offset + 8];
        if kind == b"IHDR" && !header && offset == 8 && size == 13 {
            // This profile accepts only the archived opaque RGB8 content plane.
            if content[offset + 16..offset + 21] != [8, 2, 0, 0, 0] {
                return Err(RendererError::image("annotation PNG must be opaque RGB8"));
            }
            header = true;
        } else if kind == b"IDAT" && header && !end {
            pixels = true;
        } else if kind == b"pHYs" && header && !pixels && size == 9 {
            // Immutable MuPDF certificate previews carry their 150-dpi resolution.
        } else if kind == b"IEND" && pixels && !end && size == 0 && next == content.len() {
            end = true;
        } else {
            return Err(RendererError::image(
                "annotation PNG has metadata, animation or invalid framing",
            ));
        }
        offset = next;
    }
    if !end {
        return Err(RendererError::image("annotation PNG ending is missing"));
    }
    Ok(())
}

pub(super) fn execute(
    metadata: &[u8],
    content: Vec<u8>,
    describe: bool,
) -> Result<(Value, Vec<u8>), RendererError> {
    let request: Request = serde_json::from_slice(metadata)
        .map_err(|_| RendererError::invalid("annotation request metadata is invalid"))?;
    let crop = validate(&request)?;
    let (canvas, font, lines) = layout(&request, &crop)?;
    if describe {
        if !content.is_empty() {
            return Err(RendererError::invalid("description accepts metadata only"));
        }
        return Ok((canvas, vec![]));
    }
    if content.is_empty()
        || content.len() > MAX_IMAGE_BYTES
        || !content.starts_with(b"\x89PNG\r\n\x1a\n")
        || hex_sha256(&content) != request.input_content_sha256
    {
        return Err(RendererError::image("annotation input PNG/hash is invalid"));
    }
    validate_input_png(&content)?;
    let source = decode_oriented(&content)?;
    let mut marked = if request.approval.is_some() {
        if source.dimensions() != (crop.width, crop.height) {
            return Err(RendererError::invalid(
                "release content dimensions differ from approval",
            ));
        }
        source
    } else {
        if source.dimensions()
            != (
                number(&request.source["source_png"], "width_px")?,
                number(&request.source["source_png"], "height_px")?,
            )
            || content.len() != number(&request.source["source_png"], "size_bytes")? as usize
        {
            return Err(RendererError::invalid(
                "source PNG differs from authorized archive",
            ));
        }
        let authorized = rectangle(&request.source["authorized_content"])?;
        imageops::crop_imm(
            &source,
            authorized.x + crop.x,
            authorized.y + crop.y,
            crop.width,
            crop.height,
        )
        .to_image()
    };
    if request.approval.is_none() {
        for box_rect in &request.plan.boxes {
            let local = PixelRect {
                x: box_rect.x - crop.x,
                y: box_rect.y - crop.y,
                width: box_rect.width,
                height: box_rect.height,
            };
            draw_inner_border(&mut marked, &local);
        }
    }
    let digest = pixel_sha256(&marked);
    if let Some(approval) = &request.approval {
        if approval["content_pixel_sha256"] != digest {
            return Err(RendererError::invalid(
                "release altered reviewed RGB content",
            ));
        }
    }
    let width = number(&canvas, "width_px")?;
    let height = number(&canvas, "height_px")?;
    let mut output = RgbImage::from_pixel(width, height, Rgb([255, 255, 255]));
    imageops::replace(
        &mut output,
        &marked,
        i64::from(number(&canvas["mapping"], "content_offset_x")?),
        0,
    );
    for (index, line) in lines.iter().enumerate() {
        draw_text_line(
            &mut output,
            &font,
            line,
            FOOTER_MARGIN,
            marked.height() + FOOTER_MARGIN + index as u32 * FOOTER_LINE_HEIGHT,
        );
    }
    let png = encode_png(&output)?;
    if png.len() > MAX_IMAGE_BYTES {
        return Err(RendererError::image("rendered PNG exceeds its limit"));
    }
    let receipt = json!({"renderer":request.renderer,"plan_sha256":request.plan_sha256,"provenance_sha256":request.provenance_sha256,"image":{"sha256":hex_sha256(&png),"size_bytes":png.len(),"width_px":width,"height_px":height,"media_type":"image/png"},"canvas":canvas,"content_pixel_sha256":digest,"root_mapping_sha256":root_mapping(&request,&crop)?});
    Ok((receipt, png))
}

#[cfg(test)]
pub(super) fn test_request(content: &[u8], width: u32, height: u32) -> Value {
    let id = "00000000-0000-4000-8000-000000000001";
    let timestamp = "2026-10-06T00:00:00Z";
    let image = json!({"sha256":hex_sha256(content),"size_bytes":content.len(),"width_px":width,"height_px":height,"media_type":"image/png"});
    let mut preview = image.clone();
    preview["name"] = json!("page.png");
    let source = json!({"kind":"certificate_page","archive":{"id":id,"org_id":id,"task_id":id,"task_certificate_id":id,"certificate_id":id,"certificate_revision_id":id,"certificate_file_id":id,"source_kind":"user_supplied_certificate_pdf","original":{"name":"certificate.pdf","sha256":"a".repeat(64),"size_bytes":100,"media_type":"application/pdf","page_count":1},"page":1,"render_profile":"pdf-page-preview-v1","dpi":150,"preview":preview,"rendered_at":timestamp,"created_by":id,"active_selection":true,"status":"unconfirmed_source","confirmed_by":null,"eligible_for_draft_export":false},"source_png":image,"page_kind":"pdf_page","source_time_kind":"server_page_rendered_at","source_time":timestamp,"original_sha256":"a".repeat(64),"selection_id":id,"resource_revision_id":id,"authorized_content":{"x":0,"y":0,"width":width,"height":height}});
    let plan = json!({"crop":null,"boxes":[]});
    let mut value = json!({"protocol":PROTOCOL,"source":source,"plan":plan,"plan_sha256":canonical_sha256(&plan).unwrap(),"renderer":current_identity("annotation-candidate-v1").unwrap(),"content_mode":"source_png","input_content_sha256":hex_sha256(content),"provenance_sha256":"0".repeat(64),"approval":null});
    let request: Request = serde_json::from_value(value.clone()).unwrap();
    value["provenance_sha256"] = json!(canonical_sha256(
        &json!({"footer":footer_pairs(&request).unwrap(),"renderer":request.renderer})
    )
    .unwrap());
    value
}

#[cfg(test)]
pub(super) fn test_release(mut request: Value, receipt: &Value, content: &[u8]) -> Value {
    let id = "00000000-0000-4000-8000-000000000001";
    let mut approval = json!({"evidence_id":id,"card_id":id,"card_revision":2,"card_revision_id":id,"confirmed_by":id,"confirmed_at":"2026-10-06T00:00:00Z","candidate_annotation_id":id,"candidate_rendition_id":id,"candidate_image_sha256":receipt["image"]["sha256"],"candidate_plan_sha256":request["plan_sha256"],"candidate_content_mapping":receipt["canvas"]["mapping"],"content_pixel_sha256":receipt["content_pixel_sha256"],"root_mapping_sha256":receipt["root_mapping_sha256"],"reviewed_region":{"x":0,"y":0,"width":1,"height":1},"requirement":{"requirement_id":id,"review_revision":1,"review_hash":"b".repeat(64),"state":"confirmed","source_binding_sha256":"c".repeat(64)},"decision_kind":"single_domain","review_domain":"commercial","co_sign":null,"card_content_sha256":"d".repeat(64),"evidence_binding_sha256":"e".repeat(64)});
    approval["approval_binding_sha256"] = json!(canonical_sha256(&approval).unwrap());
    request["approval"] = approval;
    request["renderer"] =
        serde_json::to_value(current_identity("annotation-release-v1").unwrap()).unwrap();
    request["content_mode"] = json!("marked_candidate_content");
    request["input_content_sha256"] = json!(hex_sha256(content));
    let parsed: Request = serde_json::from_value(request.clone()).unwrap();
    request["provenance_sha256"] = json!(canonical_sha256(
        &json!({"footer":footer_pairs(&parsed).unwrap(),"renderer":parsed.renderer})
    )
    .unwrap());
    request
}
