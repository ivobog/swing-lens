(function () {
  const CHART_HEIGHT = 520;
  const COLORS = {
    text: "#18202a",
    grid: "#eef2f7",
    border: "#d8dee8",
    up: "#0e7a5f",
    down: "#a13a3a",
    volumeUp: "rgba(14, 122, 95, 0.32)",
    volumeDown: "rgba(161, 58, 58, 0.32)",
    sma20: "#2f5f98",
    sma50: "#976b00",
    sma200: "#667085",
    stop: "#a13a3a",
    target: "#0e7a5f",
  };

  document.addEventListener("DOMContentLoaded", loadTickerChart);

  async function loadTickerChart() {
    const panel = document.querySelector(".chart-panel[data-chart-url]");
    const container = document.querySelector("#ticker-chart");
    const empty = document.querySelector("#ticker-chart-empty");
    if (!panel || !container || !empty) return;

    if (!window.LightweightCharts || !window.LightweightCharts.createChart) {
      showEmpty(empty, "Chart library could not be loaded.");
      return;
    }

    try {
      const payload = await fetchChartPayload(panel.dataset.chartUrl);
      if (!payload.bars || payload.bars.length === 0) {
        showEmpty(empty, payload.message || "No chart data available.");
        return;
      }

      renderChart(container, empty, payload);
    } catch (_error) {
      showEmpty(empty, "Chart could not be loaded.");
    }
  }

  async function fetchChartPayload(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`Chart API failed with ${response.status}`);
    return response.json();
  }

  function renderChart(container, empty, payload) {
    empty.hidden = true;

    const chart = window.LightweightCharts.createChart(container, {
      autoSize: true,
      width: container.clientWidth,
      height: CHART_HEIGHT,
      layout: {
        background: { type: "solid", color: "#ffffff" },
        textColor: COLORS.text,
      },
      grid: {
        vertLines: { color: COLORS.grid },
        horzLines: { color: COLORS.grid },
      },
      rightPriceScale: {
        borderColor: COLORS.border,
        scaleMargins: { top: 0.08, bottom: 0.28 },
      },
      timeScale: {
        borderColor: COLORS.border,
        timeVisible: false,
      },
      crosshair: {
        mode: 0,
      },
    });

    const priceSeries = addSeries(chart, "BarSeries", "addBarSeries", {
      upColor: COLORS.up,
      downColor: COLORS.down,
      thinBars: false,
    });
    priceSeries.setData(payload.bars);

    const volumeSeries = addSeries(chart, "HistogramSeries", "addHistogramSeries", {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    volumeSeries.setData(volumeData(payload.bars, payload.volume || []));
    configureVolumeScale(chart);

    addOverlay(chart, payload.overlays && payload.overlays.sma20, "SMA 20", COLORS.sma20);
    addOverlay(chart, payload.overlays && payload.overlays.sma50, "SMA 50", COLORS.sma50);
    addOverlay(chart, payload.overlays && payload.overlays.sma200, "SMA 200", COLORS.sma200);
    addRiskPriceLines(priceSeries, payload.levels || {});
    addMarkers(priceSeries, payload.markers || []);

    chart.timeScale().fitContent();
    bindResize(chart, container);
  }

  function addSeries(chart, constructorName, legacyMethod, options) {
    const library = window.LightweightCharts;
    if (chart.addSeries && library[constructorName]) {
      return chart.addSeries(library[constructorName], options);
    }
    if (chart[legacyMethod]) {
      return chart[legacyMethod](options);
    }
    throw new Error(`Lightweight Charts does not support ${constructorName}`);
  }

  function configureVolumeScale(chart) {
    const volumeScale = chart.priceScale && chart.priceScale("volume");
    if (!volumeScale || !volumeScale.applyOptions) return;
    volumeScale.applyOptions({
      scaleMargins: { top: 0.76, bottom: 0 },
      borderVisible: false,
    });
  }

  function addOverlay(chart, data, title, color) {
    if (!data || data.length === 0) return;
    const series = addSeries(chart, "LineSeries", "addLineSeries", {
      color,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      title,
    });
    series.setData(data);
  }

  function addRiskPriceLines(priceSeries, levels) {
    if (levels.stop !== undefined && levels.stop !== null) {
      priceSeries.createPriceLine({
        price: levels.stop,
        color: COLORS.stop,
        lineWidth: 2,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "Stop",
      });
    }

    if (levels.target !== undefined && levels.target !== null) {
      priceSeries.createPriceLine({
        price: levels.target,
        color: COLORS.target,
        lineWidth: 2,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "Target",
      });
    }
  }

  function addMarkers(priceSeries, markers) {
    if (!markers || markers.length === 0) return;
    if (priceSeries.setMarkers) {
      priceSeries.setMarkers(markers);
      return;
    }
    if (window.LightweightCharts.createSeriesMarkers) {
      window.LightweightCharts.createSeriesMarkers(priceSeries, markers);
    }
  }

  function volumeData(bars, volume) {
    const barsByTime = new Map(bars.map((bar) => [bar.time, bar]));
    return volume.map((point) => {
      const bar = barsByTime.get(point.time);
      const rising = !bar || Number(bar.close) >= Number(bar.open);
      return {
        time: point.time,
        value: point.value,
        color: rising ? COLORS.volumeUp : COLORS.volumeDown,
      };
    });
  }

  function bindResize(chart, container) {
    if (!window.ResizeObserver || chart.options && chart.options().autoSize) return;
    const resizeObserver = new ResizeObserver(() => {
      chart.applyOptions({
        width: container.clientWidth,
        height: container.clientHeight || CHART_HEIGHT,
      });
    });
    resizeObserver.observe(container);
  }

  function showEmpty(empty, message) {
    empty.textContent = message;
    empty.hidden = false;
  }
})();
