// Cases defined before implementing B05: fixed-width canvas prediction; canonical
// plan hashing without legacy redactions; release preserving reviewed RGB bytes;
// provenance/approval/identity tampering rejection; bounded expanded footer;
// unknown request keys and bool/float pixel values rejection; legacy byte stability.
#[test]
fn content_digest_binds_dimensions_and_rgb() {
    let image = image::RgbImage::from_pixel(2, 3, image::Rgb([1, 2, 3]));
    let same_bytes_different_shape = image::RgbImage::from_pixel(3, 2, image::Rgb([1, 2, 3]));
    assert_ne!(
        super::annotation::pixel_sha256(&image),
        super::annotation::pixel_sha256(&same_bytes_different_shape)
    );
}

#[test]
fn annotation_plan_excludes_legacy_redactions() {
    let plan = serde_json::json!({"crop":null,"boxes":[]});
    assert_eq!(
        super::annotation::canonical_sha256(&plan).unwrap(),
        super::hex_sha256(b"{\"boxes\":[],\"crop\":null}")
    );
}

#[test]
fn unknown_and_non_integer_plan_fields_fail() {
    for value in [
        serde_json::json!({"crop":null,"boxes":[],"redact":[]}),
        serde_json::json!({"crop":{"x":true,"y":0,"width":1,"height":1},"boxes":[]}),
        serde_json::json!({"crop":{"x":0.0,"y":0,"width":1,"height":1},"boxes":[]}),
    ] {
        assert!(serde_json::from_value::<super::annotation::Plan>(value).is_err());
    }
}

#[test]
fn prediction_matches_fixed_footer_without_source_bytes() {
    let image = image::RgbImage::from_pixel(8, 6, image::Rgb([17, 34, 51]));
    let png = super::encode_png(&image).unwrap();
    let value = super::annotation::test_request(&png, 8, 6);
    let metadata = serde_json::to_vec(&value).unwrap();
    let (predicted, no_png) = super::annotation::execute(&metadata, vec![], true).unwrap();
    assert!(no_png.is_empty());
    assert_eq!(predicted["width_px"], 1024);
    assert_eq!(predicted["mapping"]["content_offset_x"], 508);
    let (receipt, rendered) = super::annotation::execute(&metadata, png.clone(), false).unwrap();
    assert_eq!(receipt["canvas"], predicted);
    assert_eq!(
        receipt["content_pixel_sha256"],
        super::annotation::pixel_sha256(&image)
    );
    assert_eq!(
        super::annotation::execute(&metadata, png, false).unwrap().1,
        rendered
    );
}

#[test]
fn independent_request_hashes_and_unknown_nested_fields_fail() {
    let image = image::RgbImage::from_pixel(2, 2, image::Rgb([1, 2, 3]));
    let png = super::encode_png(&image).unwrap();
    let request = super::annotation::test_request(&png, 2, 2);
    for field in ["plan_sha256", "provenance_sha256", "input_content_sha256"] {
        let mut altered = request.clone();
        altered[field] = serde_json::json!("0".repeat(64));
        assert!(super::annotation::execute(
            &serde_json::to_vec(&altered).unwrap(),
            png.clone(),
            false
        )
        .is_err());
    }
    let mut altered = request.clone();
    altered["source"]["archive"]["unexpected_path"] = serde_json::json!("/file");
    assert!(
        super::annotation::execute(&serde_json::to_vec(&altered).unwrap(), png.clone(), false)
            .is_err()
    );
    let mut altered = request;
    altered["renderer"]["font_bundle_sha256"] = serde_json::json!("0".repeat(64));
    assert!(
        super::annotation::execute(&serde_json::to_vec(&altered).unwrap(), png, false).is_err()
    );
}

#[test]
fn complete_canvas_overflow_is_rejected_before_image_decode() {
    let request = super::annotation::test_request(b"no-image-needed", 1, 8192);
    assert!(
        super::annotation::execute(&serde_json::to_vec(&request).unwrap(), vec![], true).is_err()
    );
}

#[test]
fn release_preserves_pixels_and_rejects_modified_human_approval() {
    let image = image::RgbImage::from_pixel(8, 6, image::Rgb([17, 34, 51]));
    let marked = super::encode_png(&image).unwrap();
    let request = super::annotation::test_request(&marked, 8, 6);
    let (candidate, _) = super::annotation::execute(
        &serde_json::to_vec(&request).unwrap(),
        marked.clone(),
        false,
    )
    .unwrap();
    let release = super::annotation::test_release(request, &candidate, &marked);
    let (receipt, _) = super::annotation::execute(
        &serde_json::to_vec(&release).unwrap(),
        marked.clone(),
        false,
    )
    .unwrap();
    assert_eq!(
        receipt["content_pixel_sha256"],
        candidate["content_pixel_sha256"]
    );
    assert_eq!(
        receipt["root_mapping_sha256"],
        candidate["root_mapping_sha256"]
    );
    let mut changed = release;
    changed["approval"]["card_revision"] = serde_json::json!(3);
    assert!(
        super::annotation::execute(&serde_json::to_vec(&changed).unwrap(), marked, false).is_err()
    );
}
