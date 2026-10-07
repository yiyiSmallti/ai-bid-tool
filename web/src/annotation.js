import { orgRequest } from "./org.js";

/** @typedef {{kind:'certificate_page', archive:{id:string,preview:{sha256:string}}}|{kind:'annotation_candidate', annotationId:string, expectedHash?:string}|{kind:'annotation_release', releaseId:string, expectedHash?:string}|{kind:'screenshot_rendition',assetId:string,renditionId:string,expectedHash:string}} AuthorizedImageSource */
/**
 * Read one immutable source variant; IDs never enter tender document routes.
 * @param {AuthorizedImageSource} source
 * @param {AbortSignal} [signal]
 * @returns {Promise<Blob>}
 */
export async function loadAuthorizedImage(source, signal) {
  let route, expectedHash="expectedHash" in source ? source.expectedHash : undefined;
  if (source.kind === "certificate_page") {
    route = `/evidence-sources/${source.archive.id}/preview/download-link`;
    expectedHash = source.archive.preview.sha256;
  } else if (source.kind === "annotation_candidate") route = `/annotations/${source.annotationId}/preview`;
  else if (source.kind === "annotation_release") route = `/annotation-releases/${source.releaseId}/preview`;
  else if (source.kind === "screenshot_rendition") { route=`/screenshot-renditions/${source.renditionId}/preview-link`; expectedHash=source.expectedHash; }
  else throw new Error("图片来源种类不受支持");
  const result = await orgRequest(source.kind === "screenshot_rendition" ? "POST" : "GET", route, undefined, { signal, contractVersion: 4 });
  const link = result.data;
  expectedHash ??= link.image.sha256;
  const url = new URL(link.url, window.location.origin);
  const expectedPath = source.kind === "screenshot_rendition" ? `/screenshot-renditions/${source.renditionId}/content` : source.kind === "certificate_page" ? `/evidence-sources/${source.archive.id}/preview/download` : source.kind === "annotation_candidate" ? `/annotations/${source.annotationId}/content` : `/annotation-releases/${source.releaseId}/content`;
  if (url.origin !== window.location.origin || url.pathname !== expectedPath || url.hash || link.expires_in !== 300 || [...url.searchParams.keys()].join() !== "signature" || !url.searchParams.get("signature")) throw new Error("服务返回了不受支持的图片链接");
  const blob = await orgRequest("GET", link.url, undefined, { binary: true, signal, contractVersion: 4 });
  if (blob.type !== "image/png" || blob.size > 41943040 || (await blob.slice(0,8).arrayBuffer()).byteLength !== 8) throw new Error("图片类型或大小不符合契约");
  const bytes=await blob.arrayBuffer(), signature=new Uint8Array(bytes,0,8);
  if (signature.join() !== "137,80,78,71,13,10,26,10") throw new Error("预览不是有效 PNG");
  const hash=[...new Uint8Array(await crypto.subtle.digest("SHA-256",bytes))].map(v=>v.toString(16).padStart(2,"0")).join("");
  if(hash!==expectedHash)throw new Error("图片哈希与固定来源不符");
  return blob;
}
export async function imageDataUrl(blob) {
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error("图片读取失败"));reader.readAsDataURL(blob);});
}
export const annotationRequest=(method,path,body,signal)=>orgRequest(method,path,body,{signal,contractVersion:4});
