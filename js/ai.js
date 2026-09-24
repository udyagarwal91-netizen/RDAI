// Optional AI parser: sends the transcript to Claude, which separates small
// talk from the order and maps spoken names to catalog ids. Used when the
// user has saved an Anthropic API key in Settings; otherwise the offline
// parser in parser.js is used.
import { CATALOG, CATALOG_BY_ID } from './catalog.js';
import { newLineId } from './parser.js';

export const DEFAULT_MODEL = 'claude-opus-5';
const SDK_URL = 'https://cdn.jsdelivr.net/npm/@anthropic-ai/sdk/+esm';

function catalogText() {
  return CATALOG.map((p) => {
    const sizes = Object.entries(p.priceGroups).map(([k, r]) => `${k}@${r}`).join(' ');
    return `${p.id} | ${p.name} | shape ${p.shape || '-'} | ${p.unit} | ${sizes}`;
  }).join('\n');
}

const SYSTEM = `You turn a recorded sales conversation (Indian English / Hinglish, speech-to-text, so expect mis-heard words) between a hosiery sales representative of Skipper Hosiery and a retail customer into a written order.

How the order form works:
- Each order line is a product ("style") plus quantities per size. Quantities are boxes (dozens for Sofiyaa / Honey items).
- Adult sizes are 75 80 85 90 95 100 105 110 115 120 125 130. Kids sizes are 30 35 40 45 50 55 60 65 70 73 75 80 85.
- Typical speech: "Ruby IWD 85 2, 90 3" (size then qty), "85 se 100 tak 2 2 box" / "85 to 100 two each" (range, same qty), "85 90 95 100 - 2 3 2 2" (list of sizes then list of qtys). Ranges skip size 73 unless it is said.
- "RN" = round neck vest, "RNS" = round neck with sleeves, "O/E" / "I/E" = outer / inner elastic.
- Some styles are not in the price list (e.g. "JFS 2409", "AHW 613", "SP 42", "ADT 701"). Keep them with product_id "" and the spoken code in style_code. A bare number like "2505" right after "JFS 2409" means "JFS 2505". The shape for those is usually "Net" or "WSP (-15%)" (wholesale price less a percentage); "same"/"ditto" repeats the previous line's shape.
- Later corrections override earlier ones ("make 85 three", "cancel Lite brief").
- Ignore greetings, rate questions, gossip, payment talk and anything that is not an order.

Price list catalog (id | name | shape | unit | size-group@rate):
${catalogText()}

Pick the catalog id that best matches each spoken product. Only use ids from this list. If the brand is not repeated ("Ruby IWD ... ICD pocket"), assume the previous brand. Put anything you are unsure about in the line's note.`;

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['customer', 'lines', 'ignored_talk'],
  properties: {
    customer: {
      type: 'object',
      additionalProperties: false,
      required: ['name', 'place', 'transport'],
      properties: {
        name: { type: 'string', description: 'Shop / party name, empty if not said' },
        place: { type: 'string', description: 'Town or address, empty if not said' },
        transport: { type: 'string', description: 'Transport / carrier name, empty if not said' },
      },
    },
    lines: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['product_id', 'style_code', 'shape', 'quantities', 'note'],
        properties: {
          product_id: { type: 'string', description: 'Catalog id, or empty string if the style is not in the catalog' },
          style_code: { type: 'string', description: 'Style code as spoken when not in the catalog, e.g. "JFS 2409"' },
          shape: { type: 'string', description: 'Shape column text, e.g. "RN", "O/E", "Net", "WSP (-15%)"' },
          quantities: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              required: ['size', 'qty'],
              properties: { size: { type: 'integer' }, qty: { type: 'integer' } },
            },
          },
          note: { type: 'string' },
        },
      },
    },
    ignored_talk: { type: 'string', description: 'One short sentence on what non-order talk was skipped' },
  },
};

let clientPromise = null;
let clientKey = null;

async function getClient(apiKey) {
  if (!clientPromise || clientKey !== apiKey) {
    clientKey = apiKey;
    clientPromise = import(/* @vite-ignore */ SDK_URL).then((mod) => {
      const Anthropic = mod.default || mod.Anthropic;
      // The key lives only on this device (localStorage); requests go
      // straight from the browser to api.anthropic.com.
      return new Anthropic({ apiKey, dangerouslyAllowBrowser: true });
    });
  }
  return clientPromise;
}

/**
 * @returns {Promise<{header: object, lines: Array, warnings: string[], ignored: string[]}>}
 */
export async function parseWithClaude(transcript, { apiKey, model }) {
  if (!apiKey) throw new Error('Add your Anthropic API key in Settings to use AI mode.');
  const client = await getClient(apiKey);
  const response = await client.beta.messages.create({
    model: model || DEFAULT_MODEL,
    max_tokens: 16000,
    betas: ['server-side-fallback-2026-07-01'],
    fallbacks: 'default',
    output_config: { effort: 'medium', format: { type: 'json_schema', schema: SCHEMA } },
    system: [{ type: 'text', text: SYSTEM, cache_control: { type: 'ephemeral' } }],
    messages: [{ role: 'user', content: `Conversation transcript:\n\n${transcript}` }],
  });

  if (response.stop_reason === 'refusal') throw new Error('Claude declined this transcript. Use the offline parser instead.');
  if (response.stop_reason === 'max_tokens') throw new Error('Transcript too long for one request - split the conversation.');
  const block = response.content.find((b) => b.type === 'text');
  if (!block) throw new Error('No answer from Claude.');
  const data = JSON.parse(block.text);

  const warnings = [];
  const lines = [];
  for (const l of data.lines) {
    const qty = {};
    for (const { size, qty: q } of l.quantities) if (q > 0) qty[size] = q;
    if (!Object.keys(qty).length) continue;
    const p = l.product_id && CATALOG_BY_ID[l.product_id];
    const sizes = Object.keys(qty).map(Number);
    const kids = (p && p.section === 'kids' && sizes.every((s) => s <= 85)) || sizes.some((s) => s < 75 || s === 73);
    const desc = p ? p.short : (l.style_code || l.product_id || '?');
    lines.push({
      id: newLineId(),
      productId: p ? p.id : null,
      desc,
      shape: l.shape || (p ? p.shape : ''),
      section: kids ? 'kids' : 'adult',
      qty,
      custom: !p,
      customRate: null,
    });
    if (l.note) warnings.push(`${desc}: ${l.note}`);
  }
  const c = data.customer || {};
  return {
    header: { name: c.name || undefined, place: c.place || undefined, transport: c.transport || undefined },
    lines,
    warnings,
    ignored: data.ignored_talk ? [data.ignored_talk] : [],
  };
}
