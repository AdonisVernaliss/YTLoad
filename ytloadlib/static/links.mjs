export function parseLinks(text) {
  text = text.replace(/\[[^\]\r\n]*\]\((https?:\/\/[^\s<>]+)\)/g, '$1');
  text = text.replace(/<(https?:\/\/[^<>\s]+)>/g, '$1');
  return [...new Set(text.split(/\s+/).map((url) => url.trim()).filter(Boolean))];
}
