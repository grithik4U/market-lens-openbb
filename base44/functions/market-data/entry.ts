import { createClientFromRequest } from "npm:@base44/sdk";

// Deploy into the Base44 app through its builder/sandbox when supported by plan.
// MARKET_LENS_BACKEND_URL and MARKET_LENS_API_KEY are server-side secrets.
Deno.serve(async (req) => {
  try {
    const base44 = createClientFromRequest(req);
    const user = await base44.auth.me().catch(() => null);
    if (!user) return Response.json({ status: "unauthorized" }, { status: 401 });
    const origin = Deno.env.get("MARKET_LENS_BACKEND_URL");
    const key = Deno.env.get("MARKET_LENS_API_KEY");
    if (!origin || !key) return Response.json({
      status: "not_configured", message: "OpenBB backend hosting is not connected yet.",
    });
    const base = new URL(origin);
    if (base.protocol !== "https:" || base.username || base.password || base.search || base.hash) {
      return Response.json({ status: "configuration_error" }, { status: 503 });
    }
    const input = await req.json().catch(() => ({}));
    const operation = input.operation || "status";
    const symbol = input.symbol || "BTCUSD";
    const paths: Record<string, string> = {
      status: "/v1/status",
      btc_quote: "/v1/market/BTCUSD",
      gold_quote: "/v1/market/XAUUSD",
      candles: "/v1/candles/BTCUSD",
      history: "/v1/history/BTCUSD",
      treasury: "/v1/treasury",
    };
    if (!["BTCUSD", "XAUUSD"].includes(symbol)) return Response.json({ status: "invalid_symbol" }, { status: 400 });
    let path = paths[operation];
    if (operation === "macro" && ["DGS10", "DFII10", "CPIAUCSL", "DFF", "DTWEXBGS"].includes(input.series)) {
      path = `/v1/macro/${input.series}`;
    }
    if (!path) return Response.json({ status: "invalid_operation" }, { status: 400 });
    const upstream = await fetch(new URL(path, base), {
      headers: { "X-API-Key": key }, signal: AbortSignal.timeout(55000), redirect: "error",
    });
    if (!upstream.ok) return Response.json({ status: "unavailable", upstream_status: upstream.status }, { status: 502 });
    return Response.json(await upstream.json());
  } catch {
    return Response.json({ status: "unavailable", message: "Data service could not be reached." }, { status: 502 });
  }
});
