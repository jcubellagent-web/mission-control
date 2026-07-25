#!/usr/bin/env python3
"""Verify the rendered Control Tower React kiosk and publish a truthful status.

The check prefers Playwright because it can observe the DOM, console, failed
requests, and viewport overflow.  Dedicated hosts without Playwright fall back
to an installed Chromium browser's ``--dump-dom`` mode.  That fallback still
proves the React page rendered, but is reported as *degraded* because console,
network, and element-level overflow inspection are unavailable.

Screenshot capture is evidence, not a proxy for DOM correctness.  A skipped or
permission-blocked physical screenshot is never recorded as a pass.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUT = DATA / "mission-control-runtime-layout.json"
LIVE_DATA = DATA / "control-tower-live.json"
DASHBOARD_FALLBACK = DATA / "dashboard-data.json"
DEFAULT_KIOSK_URL = "http://127.0.0.1:5174/"
REQUIRED_TEXT = (
    "Josh 2.0 | Control Tower",
    "Live Work Board",
    "Today's Jobs",
    "Brain Atlas",
    "FinOps Dashboard",
)
REQUIRED_IDS = ("brain-feed", "today-jobs", "brain-atlas", "finops-dashboard")
REQUIRED_ARIA_LABELS = ("Control Tower summary", "Live Work Board")
#JAIMES: Keep kiosk-distance typography and FinOps geometry in the permanent
# 1920x1080 runtime guard so compact desktop/mobile checks cannot mask a regression.
KIOSK_PROBE_LABEL = "kiosk-1920"
KIOSK_VIEWPORT = {"width": 1920, "height": 1080}
REFERENCE_PROBE_LABEL = "reference-2048"
REFERENCE_VIEWPORT = {"width": 2048, "height": 1228}
REDUCED_MOTION_PROBE_LABEL = "kiosk-reduced-motion"
MEMORY_ACTIVITY_MAX_AGE_SECONDS = 100.0
KIOSK_LEGIBILITY_THRESHOLDS = {
    "liveObjectiveFont": 24.0,
    "liveNameFont": 17.0,
    "liveDescriptionFont": 12.5,
    "liveSecondaryFont": 10.5,
    "finopsBottomDeadSpace": 10.0,
    "finopsWalletWidthMin": 640.0,
    "finopsWalletWidthMax": 700.0,
    "providerNameFont": 12.0,
    "providerBodyFont": 8.0,
    "providerMetadataFont": 8.0,
    "providerCardWidth": 190.0,
    "providerCardHeight": 118.0,
    "ledgerRowHeight": 22.0,
    "healthHeightMin": 54.0,
    "healthHeightMax": 58.0,
    "atlasUnifiedMapHeight": 300.0,
    "atlasHorizontalFillRatio": 0.9,
    "atlasPrimaryGlyphHeight": 8.0,
    "atlasSecondaryGlyphHeight": 7.0,
    "atlasSectionHeadingFont": 11.0,
    "atlasSectionDescriptionFont": 9.0,
}
INTERNAL_TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private filesystem path", re.compile(r"(?:/Users/|/home/)[^\s<]{2,}", re.I)),
    ("Python traceback", re.compile(r"Traceback \(most recent call last\)", re.I)),
    ("source stack", re.compile(r"(?:node_modules/|(?:src|scripts)/[^\s:]+:\d+)", re.I)),
    ("legacy product label", re.compile(r"(?:React v2 Mission Control|Mission Control v2|Local legacy fallback)", re.I)),
    ("secret-shaped text", re.compile(r"(?:Bearer\s+[A-Za-z0-9._~-]{12,}|\bsk-[A-Za-z0-9_-]{12,}|(?:api[_ -]?key|client_secret|refresh_token)\s*[:=]\s*\S+)", re.I)),
)
PROOF_WORK_LABEL_UNSAFE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[\x00-\x1f\x7f]"),
    re.compile(r"\bhttps?://", re.I),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"(?:^|\s)(?:/(?:Users|home|var|private|tmp|etc|opt)/|~/|[A-Z]:\\)", re.I),
    re.compile(r"/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+"),
    re.compile(r"\b\d{3}[- .]\d{2}[- .]\d{4}\b"),
    re.compile(r"(?:\+?\d[\d(). -]{7,}\d)"),
    re.compile(r"(?:Bearer\s+[A-Za-z0-9._~-]{12,}|\bsk-[A-Za-z0-9_-]{12,}|(?:api[_ -]?key|client_secret|refresh_token)\s*[:=]\s*\S+)", re.I),
    re.compile(r"\b(?:work|run|event|receipt)-[a-z0-9_-]{6,}\b", re.I),
    re.compile(r"\b[a-f0-9]{16,}\b", re.I),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
)


KIOSK_LEGIBILITY_EVALUATION = r"""() => {
  const root = document.documentElement;
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none'
      && style.visibility !== 'hidden'
      && Number.parseFloat(style.opacity || '1') > 0
      && rect.width > 0
      && rect.height > 0;
  };
  const round = (value) => Math.round(Number(value || 0) * 100) / 100;
  const measurements = (selector, ownerSelector = '') => [...document.querySelectorAll(selector)]
    .filter(visible)
    .map((element, index) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const owner = ownerSelector ? element.closest(ownerSelector) : null;
      const ownerRect = owner ? owner.getBoundingClientRect() : null;
      const overflowX = Math.max(0, element.scrollWidth - element.clientWidth);
      const overflowY = Math.max(0, element.scrollHeight - element.clientHeight);
      const lineHeight = Number.parseFloat(style.lineHeight);
      const outsideOwner = Boolean(ownerRect) && (
        rect.left < ownerRect.left - 1
        || rect.right > ownerRect.right + 1
        || rect.top < ownerRect.top - 1
        || rect.bottom > ownerRect.bottom + 1
      );
      const titledEllipsis = Boolean(element.getAttribute('title'))
        && ['ellipsis', 'clip'].includes(style.textOverflow)
        && style.whiteSpace === 'nowrap';
      const lineBoxClipped = Number.isFinite(lineHeight) && rect.height + 1 < lineHeight;
      return {
        index,
        fontSize: round(Number.parseFloat(style.fontSize)),
        overflowX: round(overflowX),
        overflowY: round(overflowY),
        clipped: lineBoxClipped || outsideOwner || (!titledEllipsis && (overflowX > 1 || overflowY > 1)),
      };
    });
  const panelRect = (selector) => {
    const element = document.querySelector(selector);
    if (!visible(element)) return null;
    const rect = element.getBoundingClientRect();
    return {
      top: round(rect.top),
      left: round(rect.left),
      right: round(rect.right),
      bottom: round(rect.bottom),
      width: round(rect.width),
      height: round(rect.height),
      fullyInViewport: rect.top >= -1 && rect.left >= -1
        && rect.right <= root.clientWidth + 1 && rect.bottom <= root.clientHeight + 1,
    };
  };
  const panel = document.querySelector('#finops-dashboard');
  const body = document.querySelector('#finops-dashboard .finops-command-grid');
  const wallet = document.querySelector('#finops-dashboard [data-finops-region="wallet"]');
  const ledger = document.querySelector('#finops-dashboard [data-finops-region="ledger"]');
  const health = document.querySelector('#finops-dashboard [data-finops-region="health"]');
  const providerCards = [...document.querySelectorAll('#finops-dashboard [data-finops-region="provider"]')].filter(visible);
  const metricBands = [...document.querySelectorAll('#finops-dashboard [data-finops-metric-band]')].filter(visible);
  const ledgerRows = [...document.querySelectorAll('#finops-dashboard .finops-ledger-row')].filter(visible);
  const finopsRect = panel ? panel.getBoundingClientRect() : null;
  const bodyRect = body ? body.getBoundingClientRect() : null;
  const walletRect = wallet ? wallet.getBoundingClientRect() : null;
  const healthRect = health ? health.getBoundingClientRect() : null;
  const ledgerNodes = ledger
    ? [ledger, ...ledger.querySelectorAll('.finops-ledger-rows, .finops-ledger-row')].filter(visible)
    : [];
  const ledgerOverflowX = ledgerNodes.length
    ? Math.max(...ledgerNodes.map((element) => Math.max(0, element.scrollWidth - element.clientWidth)))
    : null;
  const ledgerOverflowY = ledgerNodes.length
    ? Math.max(...ledgerNodes.map((element) => Math.max(0, element.scrollHeight - element.clientHeight)))
    : null;
  const visibleDetailFeeds = [...document.querySelectorAll('#finops-dashboard .finops-trade-ledger, #finops-dashboard .finops-activity-journal')]
    .filter(visible).length;
  const providerGeometry = providerCards.map((element) => {
    const rect = element.getBoundingClientRect();
    return {
      provider: element.getAttribute('data-provider'),
      active: element.getAttribute('data-active'),
      width: round(rect.width),
      height: round(rect.height),
      overflowX: round(Math.max(0, element.scrollWidth - element.clientWidth)),
      overflowY: round(Math.max(0, element.scrollHeight - element.clientHeight)),
      routeColor: getComputedStyle(element).getPropertyValue('--route-color').trim().toUpperCase(),
    };
  });
  const jobsScroller = document.querySelector('#today-jobs .today-jobs-scroll');
  const nowMarker = document.querySelector('#today-jobs [data-now-marker="current"]');
  const nonGreenRows = [...document.querySelectorAll('#today-jobs .today-job-row:not(.is-complete)')];
  const nonGreenSummaries = [...document.querySelectorAll('#today-jobs .today-jobs-summary [data-reason-trigger="true"]')];
  const reasonTriggers = [...document.querySelectorAll('#today-jobs [data-reason-trigger="true"]')];
  const reasonText = reasonTriggers.map((element) => String(element.getAttribute('data-reason') || '').trim());
  const jobsRect = jobsScroller ? jobsScroller.getBoundingClientRect() : null;
  const nowRect = nowMarker ? nowMarker.getBoundingClientRect() : null;
  const directChildrenValid = jobsScroller
    ? [...jobsScroller.children].every((element) => element.getAttribute('role') === 'row' || element.classList.contains('today-jobs-empty'))
    : false;
  const livePanelRect = panelRect('#brain-feed');
  const jobsPanelRect = panelRect('#today-jobs');
  const atlasPanelRect = panelRect('#brain-atlas');
  const finopsPanelRect = panelRect('#finops-dashboard');
  const memoryMap = document.querySelector('#brain-atlas .memory-flow-map');
  const memoryMapStyle = memoryMap ? getComputedStyle(memoryMap) : null;
  const declaredJobsLabel = document.querySelector('#today-jobs .today-jobs-summary')?.getAttribute('aria-label') || '';
  const declaredJobsMatch = declaredJobsLabel.match(/^(\d+)\s+job occurrences today$/i);
  const memoryEdges = [...document.querySelectorAll('#brain-atlas .memory-flow-edge')].map((element) => {
    const observedAt = String(element.getAttribute('data-observed-at') || '');
    const observedMs = Date.parse(observedAt);
    const style = getComputedStyle(element);
    const animationName = style.animationName;
    return {
      agent: String(element.getAttribute('data-agent') || ''),
      operation: String(element.getAttribute('data-operation') || ''),
      observedAt,
      evidenceValid: Boolean(observedAt) && Number.isFinite(observedMs),
      ageSeconds: Number.isFinite(observedMs) ? round((Date.now() - observedMs) / 1000) : null,
      live: element.classList.contains('is-live'),
      animationName,
      animated: animationName !== 'none',
      strokeWidth: round(Number.parseFloat(style.strokeWidth)),
      strokeDasharray: style.strokeDasharray,
      strokeLinecap: style.strokeLinecap,
      stroke: style.stroke,
      filter: style.filter,
    };
  });
  const atlasAgentNodes = [...document.querySelectorAll('#brain-atlas .memory-flow-node.is-agent')].map((element) => {
    const aura = element.querySelector('.memory-flow-node-aura');
    const presenceDot = element.querySelector('.memory-flow-presence-dot');
    const memoryReceiptDot = element.querySelector('.memory-flow-memory-receipt-dot');
    const shell = [...element.children].find((child) => child.tagName.toLowerCase() === 'rect' && !child.classList.contains('memory-flow-node-aura'));
    const auraAnimationName = aura ? getComputedStyle(aura).animationName : 'none';
    const presenceAnimationName = presenceDot ? getComputedStyle(presenceDot).animationName : 'none';
    const memoryReceiptOpacity = memoryReceiptDot ? Number.parseFloat(getComputedStyle(memoryReceiptDot).opacity) : 0;
    const shellStyle = shell ? getComputedStyle(shell) : null;
    const memoryAnimationName = shellStyle?.animationName || 'none';
    return {
      agent: String(element.getAttribute('data-agent') || ''),
      layer: String(element.closest('[data-atlas-layer]')?.getAttribute('data-atlas-layer') || ''),
      working: element.getAttribute('data-agent-working') === 'true',
      workState: String(element.getAttribute('data-work-state') || ''),
      memoryState: String(element.getAttribute('data-memory-state') || ''),
      workClass: element.classList.contains('is-work-active'),
      memoryClass: element.classList.contains('is-memory-live'),
      memoryReceiptVisible: memoryReceiptOpacity > 0,
      auraAnimationName,
      presenceAnimationName,
      memoryAnimationName,
      memoryFilter: shellStyle?.filter || 'none',
      memoryStrokeWidth: shellStyle ? round(Number.parseFloat(shellStyle.strokeWidth)) : 0,
      workAnimated: auraAnimationName !== 'none' || presenceAnimationName !== 'none',
      memoryAnimated: memoryAnimationName !== 'none',
      animated: auraAnimationName !== 'none' || presenceAnimationName !== 'none' || memoryAnimationName !== 'none',
    };
  });
  const liveWorkAgents = [...document.querySelectorAll('.brain-hero.is-flight-deck .agent-hero-card')].map((element) => {
    const modelChip = element.querySelector('.agent-controller-model');
    const workerModels = [...element.querySelectorAll('.agent-worker-model')];
    const workerOverflow = element.querySelector('.agent-worker-overflow');
    const header = element.querySelector('header');
    return {
      agent: String(element.getAttribute('data-agent') || ''),
      working: element.getAttribute('data-agent-working') === 'true',
      modelFamily: String(element.getAttribute('data-model-family') || ''),
      modelVerified: element.getAttribute('data-model-verified') === 'true',
      modelLabel: String(modelChip?.textContent || '').trim(),
      modelChipFamily: String(modelChip?.getAttribute('data-model-family') || ''),
      modelChipVerified: modelChip?.getAttribute('data-model-verified') === 'true',
      workerCount: Number(element.getAttribute('data-worker-count') || 0),
      visibleWorkerCount: workerModels.length,
      workerFamilies: workerModels.map((worker) => String(worker.getAttribute('data-model-family') || '')),
      workerLabels: workerModels.map((worker) => String(worker.getAttribute('aria-label') || '').trim()),
      workerStaleStates: workerModels.map((worker) => String(worker.getAttribute('data-worker-stale') || '')),
      workerOverflow: String(workerOverflow?.textContent || '').trim(),
      headerOverflowX: header ? round(Math.max(0, header.scrollWidth - header.clientWidth)) : null,
      headerOverflowY: header ? round(Math.max(0, header.scrollHeight - header.clientHeight)) : null,
    };
  });
  const atlasRoot = document.querySelector('#brain-atlas');
  const atlasRootRect = atlasRoot ? atlasRoot.getBoundingClientRect() : null;
  const atlasRegion = (name, graphSelector, primarySelector, secondarySelector, layerSelector = '', fillAnchorSelector = '') => {
    const element = document.querySelector(`#brain-atlas [data-atlas-region="${name}"]`);
    if (!visible(element)) return null;
    const rect = element.getBoundingClientRect();
    const labelledBy = String(element.getAttribute('aria-labelledby') || '');
    const describedBy = String(element.getAttribute('aria-describedby') || '');
    const labelledByTarget = labelledBy ? document.getElementById(labelledBy) : null;
    const heading = element.querySelector('h3');
    const description = describedBy ? document.getElementById(describedBy) : null;
    const graph = element.querySelector(graphSelector);
    const graphRect = visible(graph) ? graph.getBoundingClientRect() : null;
    const svg = graph?.querySelector('svg');
    const fillAnchorRects = svg && fillAnchorSelector
      ? [...svg.querySelectorAll(fillAnchorSelector)]
          .filter(visible)
          .map((anchor) => anchor.getBoundingClientRect())
          .filter((anchorRect) => anchorRect.width > 0 && anchorRect.height > 0)
      : [];
    const horizontalContentLeft = fillAnchorRects.length ? Math.min(...fillAnchorRects.map((anchorRect) => anchorRect.left)) : null;
    const horizontalContentRight = fillAnchorRects.length ? Math.max(...fillAnchorRects.map((anchorRect) => anchorRect.right)) : null;
    const horizontalFillRatio = graphRect && horizontalContentLeft !== null && horizontalContentRight !== null
      ? round(Math.max(0, horizontalContentRight - horizontalContentLeft) / graphRect.width)
      : null;
    const glyphHeights = (selector) => selector
      ? [...element.querySelectorAll(selector)].filter(visible).map((node) => round(node.getBoundingClientRect().height))
      : [];
    const textState = (node) => {
      if (!visible(node)) return {fontSize: null, clipped: true};
      const style = getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      const owner = node.closest('.brain-atlas-section-header');
      const ownerRect = owner ? owner.getBoundingClientRect() : null;
      const outsideOwner = Boolean(ownerRect) && (
        rect.top < ownerRect.top - 1
        || rect.bottom > ownerRect.bottom + 1
        || rect.left < ownerRect.left - 1
        || rect.right > ownerRect.right + 1
      );
      return {
        fontSize: round(Number.parseFloat(style.fontSize)),
        clipped: outsideOwner || node.scrollWidth > node.clientWidth + 1 || node.scrollHeight > node.clientHeight + 1,
      };
    };
    const headingState = textState(heading);
    const descriptionState = textState(description);
    const htmlTextTargets = atlasRoot ? [...atlasRoot.querySelectorAll(
      '.brain-atlas-header h2, .brain-atlas-legend span, .brain-atlas-state strong, '
      + '.brain-atlas-state em, .brain-atlas-section-header h3, .brain-atlas-section-header p, '
      + '.brain-atlas-scope span, .brain-atlas-evidence-summary, .memory-flow-metrics span, '
      + '.memory-flow-metrics strong, .memory-flow-metrics em, .brain-atlas-focus span, '
      + '.brain-atlas-focus select, .brain-atlas-proof-state'
    )].filter(visible) : [];
    const htmlTextOverflowRoles = htmlTextTargets.flatMap((node, index) => {
      const overflowX = Math.max(0, node.scrollWidth - node.clientWidth);
      const overflowY = Math.max(0, node.scrollHeight - node.clientHeight);
      const role = String(node.className || node.tagName || `text-${index}`)
        .trim().replace(/\s+/g, '.').slice(0, 80);
      return overflowX > 1 || overflowY > 1 ? [role || `text-${index}`] : [];
    });
    const svgTextFit = [];
    const svgTextOverlap = [];
    if (svg) {
      const textOwners = [...svg.querySelectorAll(
        '.memory-flow-node, .brain-atlas-proof-work, .brain-atlas-proof-model'
      )];
      textOwners.forEach((owner, ownerIndex) => {
        const shell = [...owner.children].find((child) => (
          child.tagName.toLowerCase() === 'rect'
          && !child.classList.contains('memory-flow-node-aura')
        ));
        if (!shell) return;
        const shellBox = shell.getBBox();
        const texts = [...owner.querySelectorAll('text')].filter(visible);
        texts.forEach((textNode, textIndex) => {
          const box = textNode.getBBox();
          const fits = box.x >= shellBox.x - 1
            && box.y >= shellBox.y - 1
            && box.x + box.width <= shellBox.x + shellBox.width + 1
            && box.y + box.height <= shellBox.y + shellBox.height + 1;
          svgTextFit.push({
            role: `${String(owner.getAttribute('class') || 'svg-owner').replace(/\s+/g, '.')}:${textIndex}`,
            fits,
          });
        });
        const title = owner.querySelector('.memory-flow-node-title, .brain-atlas-proof-title');
        const detail = owner.querySelector('.memory-flow-node-detail, .brain-atlas-proof-detail');
        if (title && detail && visible(title) && visible(detail)) {
          const titleBox = title.getBBox();
          const detailBox = detail.getBBox();
          const verticalOverlap = Math.min(titleBox.y + titleBox.height, detailBox.y + detailBox.height)
            - Math.max(titleBox.y, detailBox.y);
          if (verticalOverlap > 0.5) svgTextOverlap.push(ownerIndex);
        }
      });
    }
    const nodeBoxes = [...element.querySelectorAll('.brain-atlas-node, .brain-atlas-proof-work, .brain-atlas-proof-receipt, .brain-atlas-proof-model')].filter(visible).map((node) => {
      const box = node.getBoundingClientRect();
      const layer = ['agent', 'work', 'receipt', 'model'].find((kind) => (
        node.classList.contains(`is-${kind}`) || node.classList.contains(`brain-atlas-proof-${kind}`)
      )) || '';
      return {layer, top: box.top, bottom: box.bottom, left: box.left, right: box.right};
    });
    let nodeOverlapCount = 0;
    for (let leftIndex = 0; leftIndex < nodeBoxes.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < nodeBoxes.length; rightIndex += 1) {
        const left = nodeBoxes[leftIndex];
        const right = nodeBoxes[rightIndex];
        if (left.layer !== right.layer) continue;
        const overlapX = Math.min(left.right, right.right) - Math.max(left.left, right.left);
        const overlapY = Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top);
        if (overlapX > 1 && overlapY > 1) nodeOverlapCount += 1;
      }
    }
    return {
      top: round(rect.top),
      bottom: round(rect.bottom),
      height: round(rect.height),
      contained: Boolean(atlasRootRect)
        && rect.top >= atlasRootRect.top - 1
        && rect.bottom <= atlasRootRect.bottom + 1
        && rect.left >= atlasRootRect.left - 1
        && rect.right <= atlasRootRect.right + 1,
      heading: String(heading?.textContent || '').trim(),
      description: String(description?.textContent || '').trim(),
      headingFontSize: headingState.fontSize,
      descriptionFontSize: descriptionState.fontSize,
      headingClipped: headingState.clipped,
      descriptionClipped: descriptionState.clipped,
      labelledBy,
      describedBy,
      labelledByTargetPresent: Boolean(labelledByTarget),
      graphHeight: graphRect ? round(graphRect.height) : null,
      horizontalFillRatio,
      graphKind: svg ? 'svg' : graph ? 'empty' : 'missing',
      overflowY: round(Math.max(0, element.scrollHeight - element.clientHeight)),
      svgTitlePresent: Boolean(svg?.querySelector('title')?.textContent?.trim()),
      svgDescriptionPresent: Boolean(svg?.querySelector('desc')?.textContent?.trim()),
      primaryGlyphHeights: glyphHeights(primarySelector),
      secondaryGlyphHeights: glyphHeights(secondarySelector),
      layerGlyphHeights: glyphHeights(layerSelector),
      nodeOverlapCount,
      htmlTextOverflowCount: htmlTextOverflowRoles.length,
      htmlTextOverflowRoles,
      svgTextOverflowCount: svgTextFit.filter((item) => !item.fits).length,
      svgTextOverflowRoles: svgTextFit.filter((item) => !item.fits).map((item) => item.role),
      svgTextOverlapCount: svgTextOverlap.length,
    };
  };
  const atlasUnifiedRegion = atlasRegion(
    'unified',
    '.memory-flow-map',
    '.memory-flow-node-title, .brain-atlas-proof-title',
    '.memory-flow-node-detail, .brain-atlas-proof-detail, .brain-atlas-proof-time',
    '.brain-atlas-lane-label, .brain-atlas-proof-column',
    '.memory-flow-node, .brain-atlas-proof-work, .brain-atlas-proof-receipt, .brain-atlas-proof-model'
  );
  const visibleAtlasRegions = [...document.querySelectorAll('#brain-atlas [data-atlas-region]')].filter(visible);
  const visibleAtlasLayers = [...document.querySelectorAll('#brain-atlas [data-atlas-layer]')].filter(visible);
  const atlasLayerCounts = Object.fromEntries(
    ['memory', 'proof'].map((name) => [
      name,
      visibleAtlasLayers.filter((element) => element.getAttribute('data-atlas-layer') === name).length,
    ])
  );
  // Exact proof remains in the bounded SVG data model for audit integrity, but
  // the memory-first screen keeps it out of the always-visible layout.
  const proofRows = [...document.querySelectorAll('#brain-atlas [data-proof-row]')].map((element) => {
    const rect = element.getBoundingClientRect();
    const svg = element.closest('svg');
    const svgRect = svg ? svg.getBoundingClientRect() : null;
    const workLabel = String(element.getAttribute('data-work-label') || '').trim();
    const visibleWorkLabel = String(element.querySelector('.brain-atlas-proof-work .brain-atlas-proof-title')?.textContent || '').trim();
    return {
      agent: String(element.getAttribute('data-agent') || '').trim(),
      workLabel,
      visibleWorkLabel,
      receipt: String(element.getAttribute('data-receipt') || '').trim(),
      receiptStatus: String(element.getAttribute('data-receipt-status') || '').trim(),
      model: String(element.getAttribute('data-model') || '').trim(),
      routeVerified: element.getAttribute('data-route-verified') === 'true',
      declaredAnimated: element.getAttribute('data-proof-animated') !== 'false',
      opaqueLabel: /^Work\s+[a-f0-9]{8}$/i.test(workLabel),
      clipped: !svgRect
        || rect.left < svgRect.left - 1
        || rect.right > svgRect.right + 1
        || rect.top < svgRect.top - 1
        || rect.bottom > svgRect.bottom + 1,
    };
  });
  const proofEmpty = document.querySelector('#brain-atlas .brain-atlas-proof-empty');
  const proofAudit = document.querySelector('#brain-atlas .brain-atlas-proof-audit');
  const proofHealth = document.querySelector('#brain-atlas .brain-atlas-proof-health');
  const proofEdges = [...document.querySelectorAll('#brain-atlas .brain-atlas-proof-edge')].map((element) => {
    const style = getComputedStyle(element);
    return {
      animationName: style.animationName,
      animated: style.animationName !== 'none',
      memoryFlowClass: element.classList.contains('memory-flow-edge'),
      liveClass: element.classList.contains('is-live'),
    };
  });
  return {
    viewport: {width: root.clientWidth, height: root.clientHeight},
    pageOverflowX: round(Math.max(0, root.scrollWidth - root.clientWidth)),
    pageOverflowY: round(Math.max(0, root.scrollHeight - root.clientHeight)),
    layout: {
      liveWork: livePanelRect,
      todayJobs: jobsPanelRect,
      brainAtlas: atlasPanelRect,
      finops: finopsPanelRect,
      atlasFinopsTopDelta: atlasPanelRect && finopsPanelRect ? round(Math.abs(atlasPanelRect.top - finopsPanelRect.top)) : null,
      atlasFinopsHeightDelta: atlasPanelRect && finopsPanelRect ? round(Math.abs(atlasPanelRect.height - finopsPanelRect.height)) : null,
      jobsAboveFinopsGap: jobsPanelRect && finopsPanelRect ? round(finopsPanelRect.top - jobsPanelRect.bottom) : null,
      liveAboveAtlasGap: livePanelRect && atlasPanelRect ? round(atlasPanelRect.top - livePanelRect.bottom) : null,
    },
    memory: {
      flowState: document.querySelector('#brain-atlas')?.getAttribute('data-memory-flow-state') || '',
      reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      mapAnimationName: memoryMapStyle?.animationName || 'none',
      mapAnimated: Boolean(memoryMapStyle && memoryMapStyle.animationName !== 'none'),
      mapBoxShadow: memoryMapStyle?.boxShadow || 'none',
      evidenceSource: document.querySelector('#brain-atlas .memory-flow-map svg')?.getAttribute('data-memory-source')
        || document.querySelector('#brain-atlas .memory-flow-map svg')?.getAttribute('data-evidence-source')
        || '',
      edges: memoryEdges,
      liveEdgeCount: memoryEdges.filter((edge) => edge.live).length,
      animatedEdgeCount: memoryEdges.filter((edge) => edge.animated).length,
      animatedInactiveCount: memoryEdges.filter((edge) => edge.animated && !edge.live).length,
      atlasAgentNodes,
      liveWorkAgents,
      workingAgentCount: Number(document.querySelector('#brain-atlas')?.getAttribute('data-working-agent-count') || 0),
    },
    brainAtlasSections: {
      unified: atlasUnifiedRegion,
    },
    brainAtlasView: {
      active: String(atlasRoot?.getAttribute('data-atlas-view') || ''),
      tone: String(atlasRoot?.getAttribute('data-atlas-view-tone') || ''),
      statusText: String(document.querySelector('#brain-atlas .brain-atlas-state')?.textContent || '').trim(),
      visiblePanelCount: visibleAtlasRegions.length,
      legacyViewControlCount: document.querySelectorAll('#brain-atlas [data-atlas-view-option]').length,
      layerCounts: atlasLayerCounts,
      proofState: String(atlasRoot?.getAttribute('data-exact-proof-state') || ''),
      proofAuditVisible: Boolean(proofAudit && visible(proofAudit)),
      proofHealthVisible: Boolean(proofHealth && visible(proofHealth)),
      proofEmptyText: String(proofEmpty?.textContent || '').trim(),
      proofRows,
      proofEdges,
    },
    liveWork: {
      objectives: measurements('.brain-hero.is-flight-deck .agent-objective-main', '.agent-hero-card'),
      names: measurements('.brain-hero.is-flight-deck .agent-name-lockup strong', '.agent-hero-card'),
      descriptions: measurements('.brain-hero.is-flight-deck .agent-objective-description', '.agent-hero-card'),
      secondary: measurements('.brain-hero.is-flight-deck .agent-hero-card > p', '.agent-hero-card'),
    },
    finops: {
      bodyPresent: Boolean(finopsRect && bodyRect),
      bodyBottomDeadSpace: finopsRect && healthRect ? round(Math.max(0, finopsRect.bottom - healthRect.bottom)) : null,
      bodyBottomOvershoot: finopsRect && bodyRect ? round(Math.max(0, bodyRect.bottom - finopsRect.bottom)) : null,
      walletWidth: walletRect ? round(walletRect.width) : null,
      panelOverflowX: panel ? round(Math.max(0, panel.scrollWidth - panel.clientWidth)) : null,
      panelOverflowY: panel ? round(Math.max(0, panel.scrollHeight - panel.clientHeight)) : null,
      walletActionCount: document.querySelectorAll('#finops-dashboard .finops-wallet-action').length,
      visibleDetailFeeds,
      metricBandCount: metricBands.length,
      metricCounts: metricBands.map((element) => element.children.length),
      providerCount: providerCards.length,
      providerGeometry,
      providerNames: measurements('#finops-dashboard .finops-provider-name > strong', '.finops-provider-simple'),
      providerBodies: measurements('#finops-dashboard .finops-provider-purpose', '.finops-provider-simple'),
      providerMetadata: measurements(
        '#finops-dashboard .finops-provider-name p > span, '
        + '#finops-dashboard .finops-provider-name p > em, '
        + '#finops-dashboard .finops-provider-state strong, '
        + '#finops-dashboard .finops-provider-utilization span',
        '.finops-provider-simple'
      ),
      ledgerPresent: Boolean(ledger),
      ledgerOverflowX: ledgerOverflowX === null ? null : round(ledgerOverflowX),
      ledgerOverflowY: ledgerOverflowY === null ? null : round(ledgerOverflowY),
      ledgerRowCount: ledgerRows.length,
      ledgerRowMinHeight: ledgerRows.length ? round(Math.min(...ledgerRows.map((element) => element.getBoundingClientRect().height))) : null,
      healthPresent: Boolean(health),
      healthCount: health ? health.children.length : 0,
      healthHeight: healthRect ? round(healthRect.height) : null,
      healthOverflowX: health ? round(Math.max(0, health.scrollWidth - health.clientWidth)) : null,
      healthOverflowY: health ? round(Math.max(0, health.scrollHeight - health.clientHeight)) : null,
    },
    todayJobs: {
      rowCount: document.querySelectorAll('#today-jobs .today-job-row').length,
      declaredRowCount: declaredJobsMatch ? Number(declaredJobsMatch[1]) : null,
      nonGreenRowCount: nonGreenRows.length,
      nonGreenSummaryCount: nonGreenSummaries.length,
      reasonTriggerCount: reasonTriggers.length,
      missingReasonCount: reasonText.filter((value) => !value).length,
      objectReasonCount: reasonText.filter((value) => /\[object Object\]|undefined/i.test(value)).length,
      pendingSummaryReason: [
        document.querySelector('#today-jobs [data-summary="unverified"]')?.getAttribute('data-reason') || '',
        document.querySelector('#today-jobs [data-summary="scheduled"]')?.getAttribute('data-reason') || '',
      ].join(' ').trim(),
      nowMarkerPresent: Boolean(nowMarker),
      nowMarkerLabel: nowMarker?.getAttribute('aria-label') || '',
      scrollOverflowY: jobsScroller ? round(Math.max(0, jobsScroller.scrollHeight - jobsScroller.clientHeight)) : null,
      nowCenterDelta: jobsRect && nowRect
        ? round(Math.abs((nowRect.top + nowRect.height / 2) - (jobsRect.top + jobsRect.height / 2)))
        : null,
      followNowState: jobsScroller?.getAttribute('data-follow-now-state') || '',
      directChildrenValid,
    },
  };
}"""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def dashboard_safe_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    text = re.sub(r"(?:/Users/|/home/)[^\s'\"]+", "[private path]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~-]{12,}|\bsk-[A-Za-z0-9_-]{12,}", "[redacted credential]", text, flags=re.I)
    return text[:300] or type(exc).__name__


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return path.name


def safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def row(
    name: str,
    state: str,
    detail: str,
    *,
    required: bool = True,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if state not in {"pass", "fail", "degraded", "skipped"}:
        raise ValueError(f"unsupported check state: {state}")
    return {
        "name": name,
        "state": state,
        "ok": state != "fail",
        "required": required,
        "detail": detail,
        **({"evidence": evidence} if evidence else {}),
    }


def fetch(url: str, *, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback kiosk
        return int(response.status), response.read().decode("utf-8", errors="replace")


def check_http(url: str, timeout: float) -> dict[str, Any]:
    try:
        status, body = fetch(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - status should explain the boundary
        return row("kiosk-http", "fail", f"kiosk unreachable: {dashboard_safe_error(exc)}")
    missing = [token for token in ('id="root"', 'type="module"') if token not in body]
    if not 200 <= status < 400:
        return row("kiosk-http", "fail", f"unexpected HTTP {status}")
    if missing:
        return row("kiosk-http", "fail", f"HTTP {status}, but the Vite React shell is missing {', '.join(missing)}")
    return row("kiosk-http", "pass", f"HTTP {status}; current Vite React shell present", evidence={"bytes": len(body)})


def check_control_tower_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        return row("live-data-json", "fail", f"{path.name} failed: {dashboard_safe_error(exc)}")
    if not isinstance(data, dict):
        return row("live-data-json", "fail", "Control Tower JSON is not an object")
    object_fields = ("brainFeed", "agentBrainFeeds", "runtimeLayout")
    list_fields = ("codexJobs", "actionRequired")
    missing = [key for key in object_fields if not isinstance(data.get(key), dict)]
    missing.extend(key for key in list_fields if not isinstance(data.get(key), list))
    is_live_projection = path.name.startswith("control-tower-live")
    schedule_source = "crons" if isinstance(data.get("crons"), list) else None
    #JAIMES: Compact live data uses canonical todayJobs instead of repeating full cron rows.
    if is_live_projection and schedule_source is None and isinstance(data.get("todayJobs"), list):
        schedule_source = "todayJobs"
    if schedule_source is None:
        missing.append("crons or todayJobs" if is_live_projection else "crons")
    for stamp in ("lastUpdated", "sourceUpdatedAt"):
        if not isinstance(data.get(stamp), str) or not data.get(stamp):
            missing.append(stamp)
    if missing:
        return row("live-data-json", "fail", f"Control Tower JSON is missing canonical fields: {', '.join(missing)}")
    raw_atlas_path = path.with_name("brain-atlas.json")
    if raw_atlas_path.exists():
        try:
            raw_atlas = json.loads(raw_atlas_path.read_text())
        except Exception as exc:  # noqa: BLE001
            return row(
                "live-data-json",
                "fail",
                f"brain-atlas.json failed: {dashboard_safe_error(exc)}",
            )
        raw_status = str(raw_atlas.get("status") or "") if isinstance(raw_atlas, dict) else ""
        projected_atlas = data.get("brainAtlas") if isinstance(data.get("brainAtlas"), dict) else {}
        projected_status = str(projected_atlas.get("status") or "")
        if raw_status == "ready" and projected_status != "ready":
            projected_reason = str(projected_atlas.get("emptyReason") or "missing")
            return row(
                "live-data-json",
                "fail",
                "Brain Atlas projection rejected a ready canonical graph "
                f"(raw=ready, projected={projected_status or 'missing'}, reason={projected_reason})",
            )
    assert schedule_source is not None
    return row(
        "live-data-json",
        "pass",
        f"{path.name} parsed with canonical live-work, runtime, jobs, and source-freshness fields",
        evidence={
            "path": display_path(path),
            "brainFeed": len(data["brainFeed"]),
            "agentBrainFeeds": len(data["agentBrainFeeds"]),
            "scheduleSource": schedule_source,
            "scheduleRows": len(data[schedule_source]),
            "crons": len(data["crons"]) if isinstance(data.get("crons"), list) else 0,
            "todayJobs": len(data["todayJobs"]) if isinstance(data.get("todayJobs"), list) else 0,
            "codexJobs": len(data["codexJobs"]),
            "actionRequired": len(data["actionRequired"]),
            "lastUpdated": data.get("lastUpdated"),
            "sourceUpdatedAt": data.get("sourceUpdatedAt"),
        },
    )


class RenderedDocumentParser(HTMLParser):
    """Small fallback parser for Chromium ``--dump-dom`` evidence."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.aria_labels: set[str] = set()
        self.text_parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "template"}:
            self._ignored_depth += 1
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(str(values["id"]))
        if values.get("aria-label"):
            self.aria_labels.add(str(values["aria-label"]))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.text_parts.append(data.strip())

    @property
    def visible_text(self) -> str:
        return " ".join(self.text_parts)


def internal_text_leaks(visible_text: str) -> list[dict[str, str]]:
    leaks: list[dict[str, str]] = []
    for label, pattern in INTERNAL_TEXT_PATTERNS:
        match = pattern.search(visible_text)
        if match:
            # Avoid writing a complete secret-shaped match into dashboard-safe
            # status data.  The label and a short redacted prefix are enough.
            sample = match.group(0)
            safe_sample = "[redacted]" if label == "secret-shaped text" else "[private path]" if label == "private filesystem path" else sample[:100]
            leaks.append({"type": label, "sample": safe_sample})
    return leaks


def analyze_rendered_html(document: str) -> tuple[list[str], list[dict[str, str]], dict[str, Any]]:
    parser = RenderedDocumentParser()
    parser.feed(document)
    visible = html.unescape(parser.visible_text)
    failures: list[str] = []
    for token in REQUIRED_TEXT:
        if token not in visible:
            failures.append(f'missing visible text "{token}"')
    for token in REQUIRED_IDS:
        if token not in parser.ids:
            failures.append(f'missing #{token}')
    for token in REQUIRED_ARIA_LABELS:
        if token not in parser.aria_labels:
            failures.append(f'missing aria-label "{token}"')
    if "Control Tower hit a render error" in visible:
        failures.append("React error boundary is visible")
    leaks = internal_text_leaks(visible)
    if leaks:
        failures.append(f"{len(leaks)} visible internal-text leak(s)")
    evidence = {
        "visibleTextBytes": len(visible.encode("utf-8")),
        "requiredIdsFound": sorted(set(REQUIRED_IDS) & parser.ids),
        "requiredAriaLabelsFound": sorted(set(REQUIRED_ARIA_LABELS) & parser.aria_labels),
    }
    return failures, leaks, evidence


def ignorable_browser_request(url: str) -> bool:
    """Ignore browser chrome, not application data or assets."""
    return url.split("?", 1)[0].endswith("/favicon.ico")


def bundled_playwright_browser_missing(exc: BaseException) -> bool:
    """Return true only when Playwright's managed browser is not installed."""
    message = " ".join(str(exc).lower().split())
    return (
        "executable doesn't exist" in message
        or "executable does not exist" in message
        or (
            "playwright install" in message
            and ("download new browsers" in message or "browser" in message)
        )
    )


def launch_playwright_browser(playwright: Any) -> tuple[Any, dict[str, Any]]:
    """Launch a full Playwright browser, reusing installed Chrome if needed.

    A non-installation launch failure remains a hard failure.  This prevents a
    crash, permission error, or incompatible browser from being mislabeled as
    a healthy fallback.
    """
    try:
        browser = playwright.chromium.launch(headless=True)
        return browser, {
            "engine": "playwright-chromium",
            "browser": "bundled Chromium",
            "browserFallback": {"used": False},
        }
    except Exception as exc:  # noqa: BLE001 - classify the launch boundary
        if not bundled_playwright_browser_missing(exc):
            raise
        bundled_error = exc

    #JAIMES: Reuse installed Chrome only when Playwright's bundled executable
    # is absent; all other launch failures stay fatal.
    attempts: list[tuple[str, dict[str, str], str]] = []
    configured = os.environ.get("CONTROL_TOWER_BROWSER", "").strip()
    if configured and Path(configured).is_file():
        attempts.append(("configured executable", {"executable_path": configured}, Path(configured).name))
    attempts.append(("Chrome channel", {"channel": "chrome"}, "Google Chrome"))
    configured_resolved = str(Path(configured).resolve()) if configured and Path(configured).is_file() else ""
    for candidate in browser_candidates():
        resolved = str(Path(candidate).resolve())
        if resolved == configured_resolved:
            continue
        attempts.append(("installed executable", {"executable_path": candidate}, Path(candidate).name))

    fallback_errors: list[str] = []
    for source, launch_args, browser_name in attempts:
        try:
            browser = playwright.chromium.launch(headless=True, **launch_args)
        except Exception as exc:  # noqa: BLE001 - try the next installed browser
            fallback_errors.append(f"{source}: {dashboard_safe_error(exc)}")
            continue
        return browser, {
            "engine": "playwright-chromium",
            "browser": browser_name,
            "browserFallback": {
                "used": True,
                "from": "bundled Chromium",
                "to": source,
                "reason": "Playwright-managed browser executable was not installed",
            },
        }

    detail = "; ".join(fallback_errors) or "no installed Chrome or Chromium candidate was available"
    raise RuntimeError(
        "Playwright-managed Chromium is unavailable and installed-browser fallback failed: "
        f"{detail}"
    ) from bundled_error


def playwright_probe_specs(screenshot_path: Path | None) -> tuple[tuple[str, dict[str, int], Path | None], ...]:
    """Return responsive, physical-kiosk, reference, and accessibility probes."""
    return (
        ("desktop", {"width": 1440, "height": 1000}, screenshot_path),
        (
            "mobile",
            {"width": 390, "height": 844},
            screenshot_path.with_name(f"{screenshot_path.stem}-mobile{screenshot_path.suffix}") if screenshot_path else None,
        ),
        (
            KIOSK_PROBE_LABEL,
            dict(KIOSK_VIEWPORT),
            screenshot_path.with_name(f"{screenshot_path.stem}-{KIOSK_PROBE_LABEL}{screenshot_path.suffix}") if screenshot_path else None,
        ),
        (
            REFERENCE_PROBE_LABEL,
            dict(REFERENCE_VIEWPORT),
            screenshot_path.with_name(f"{screenshot_path.stem}-{REFERENCE_PROBE_LABEL}{screenshot_path.suffix}") if screenshot_path else None,
        ),
        (
            REDUCED_MOTION_PROBE_LABEL,
            dict(KIOSK_VIEWPORT),
            screenshot_path.with_name(f"{screenshot_path.stem}-{REDUCED_MOTION_PROBE_LABEL}{screenshot_path.suffix}") if screenshot_path else None,
        ),
    )


def _number(value: Any, *, missing: float = -1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return missing


def _px(value: float) -> str:
    return f"{value:g}px"


def _font_and_clipping_failures(
    measurements: Any,
    *,
    label: str,
    minimum: float,
) -> list[str]:
    if not isinstance(measurements, list) or not measurements:
        return [f"{KIOSK_PROBE_LABEL}: {label} measurements are missing"]
    font_sizes = [_number(item.get("fontSize")) for item in measurements if isinstance(item, dict)]
    if not font_sizes:
        return [f"{KIOSK_PROBE_LABEL}: {label} measurements are invalid"]
    failures: list[str] = []
    minimum_seen = min(font_sizes)
    if minimum_seen < minimum:
        failures.append(
            f"{KIOSK_PROBE_LABEL}: {label} minimum font is {_px(minimum_seen)} "
            f"(requires >= {_px(minimum)})"
        )
    clipped = sum(1 for item in measurements if isinstance(item, dict) and item.get("clipped") is True)
    if clipped:
        failures.append(f"{KIOSK_PROBE_LABEL}: {label} has {clipped} clipped element(s)")
    return failures


def validate_control_tower_layout(
    measurements: Any,
    *,
    label: str,
    expect_reduced_motion: bool = False,
    expect_atlas_view: str = "unified",
) -> list[str]:
    """Validate the initial four-panel composition and evidence-bound motion."""
    if not isinstance(measurements, dict):
        return [f"{label}: layout measurements are missing"]
    failures: list[str] = []

    for axis in ("pageOverflowX", "pageOverflowY"):
        overflow = _number(measurements.get(axis))
        if overflow < 0:
            failures.append(f"{label}: {axis} measurement is missing")
        elif overflow != 0:
            failures.append(f"{label}: {axis} is {_px(overflow)} (requires zero page overflow)")

    layout = measurements.get("layout") if isinstance(measurements.get("layout"), dict) else {}
    for key, panel_label in (
        ("liveWork", "Live Work"),
        ("todayJobs", "Today's Jobs"),
        ("brainAtlas", "Brain Atlas"),
        ("finops", "FinOps"),
    ):
        panel = layout.get(key)
        if not isinstance(panel, dict):
            failures.append(f"{label}: {panel_label} is not initially visible")
        elif panel.get("fullyInViewport") is not True:
            failures.append(f"{label}: {panel_label} is not fully in the initial viewport")

    for key, delta_label in (
        ("atlasFinopsTopDelta", "Brain Atlas / FinOps top delta"),
        ("atlasFinopsHeightDelta", "Brain Atlas / FinOps height delta"),
    ):
        delta = _number(layout.get(key))
        if delta < 0:
            failures.append(f"{label}: {delta_label} is missing")
        elif delta > 2:
            failures.append(f"{label}: {delta_label} is {_px(delta)} (requires <= 2px)")

    for key, order_label in (
        ("jobsAboveFinopsGap", "Today's Jobs must remain above FinOps"),
        ("liveAboveAtlasGap", "Live Work must remain above Brain Atlas"),
    ):
        gap = _number(layout.get(key))
        if gap < 0:
            failures.append(f"{label}: {order_label} (overlap {_px(abs(gap))})")

    if expect_atlas_view != "unified":
        failures.append(f"{label}: unsupported expected Brain Atlas view {expect_atlas_view!r}")
    atlas_view = measurements.get("brainAtlasView") if isinstance(measurements.get("brainAtlasView"), dict) else {}
    active_view = str(atlas_view.get("active") or "")
    if active_view != expect_atlas_view:
        failures.append(
            f"{label}: Brain Atlas active view is {active_view or 'missing'} "
            f"(requires {expect_atlas_view})"
        )
    if str(atlas_view.get("tone") or "") not in {"clear", "watch", "risk"}:
        failures.append(f"{label}: Brain Atlas unified tone is missing or invalid")
    status_text = str(atlas_view.get("statusText") or "").lower()
    if "working" not in status_text or not any(
        token in status_text for token in ("receipt", "source unavailable", "no exact receipts")
    ):
        failures.append(f"{label}: Brain Atlas unified status does not summarize work and receipt state")
    if int(_number(atlas_view.get("visiblePanelCount"), missing=-1.0)) != 1:
        failures.append(f"{label}: Brain Atlas must expose exactly one visible unified region")
    if int(_number(atlas_view.get("legacyViewControlCount"), missing=-1.0)) != 0:
        failures.append(f"{label}: Brain Atlas still exposes legacy Activity / Evidence view controls")

    layer_counts = atlas_view.get("layerCounts") if isinstance(atlas_view.get("layerCounts"), dict) else {}
    if int(_number(layer_counts.get("memory"), missing=-1.0)) != 1:
        failures.append(f"{label}: Brain Atlas must expose exactly one visible memory layer")
    if int(_number(layer_counts.get("proof"), missing=-1.0)) != 0:
        failures.append(f"{label}: Brain Atlas must keep exact proof out of the always-visible graph")
    if atlas_view.get("proofHealthVisible") is not True:
        failures.append(f"{label}: Brain Atlas proof health is not visibly summarized")
    if atlas_view.get("proofAuditVisible") is not True:
        failures.append(f"{label}: Brain Atlas exact proof audit is not available on demand")

    atlas_sections = measurements.get("brainAtlasSections") if isinstance(measurements.get("brainAtlasSections"), dict) else {}
    region = atlas_sections.get("unified")
    expected_heading = "Governed memory activity"
    region_label = "unified"
    minimum_graph_height = KIOSK_LEGIBILITY_THRESHOLDS["atlasUnifiedMapHeight"]
    if not isinstance(region, dict):
        failures.append(f"{label}: Brain Atlas {expected_heading} region is missing")
    else:
        if region.get("contained") is not True:
            failures.append(f"{label}: Brain Atlas {expected_heading} region escapes its panel")
        if str(region.get("heading") or "") != expected_heading:
            failures.append(f"{label}: Brain Atlas {expected_heading} visible heading is missing")
        description = str(region.get("description") or "").lower()
        description_tokens = ("shared memory", "recalled", "applied", "promoted", "not private reasoning")
        if not all(token in description for token in description_tokens):
            failures.append(f"{label}: Brain Atlas {expected_heading} purpose text is missing")
        if not str(region.get("labelledBy") or "") or region.get("labelledByTargetPresent") is not True:
            failures.append(f"{label}: Brain Atlas {expected_heading} accessible label is missing")
        if not region.get("describedBy"):
            failures.append(f"{label}: Brain Atlas {expected_heading} accessible description is missing")
        if _number(region.get("headingFontSize")) < KIOSK_LEGIBILITY_THRESHOLDS["atlasSectionHeadingFont"]:
            failures.append(f"{label}: Brain Atlas {expected_heading} heading is too small or unmeasured")
        if _number(region.get("descriptionFontSize")) < KIOSK_LEGIBILITY_THRESHOLDS["atlasSectionDescriptionFont"]:
            failures.append(f"{label}: Brain Atlas {expected_heading} purpose text is too small or unmeasured")
        if region.get("headingClipped") is not False or region.get("descriptionClipped") is not False:
            failures.append(f"{label}: Brain Atlas {expected_heading} heading or purpose text is clipped")
        overflow_y = _number(region.get("overflowY"))
        if overflow_y < 0:
            failures.append(f"{label}: Brain Atlas {expected_heading} overflow measurement is missing")
        elif overflow_y > 1:
            failures.append(f"{label}: Brain Atlas {expected_heading} overflows vertically by {_px(overflow_y)}")
        graph_height = _number(region.get("graphHeight"))
        if graph_height < minimum_graph_height:
            failures.append(
                f"{label}: Brain Atlas {region_label} graph height is {_px(graph_height)} "
                f"(requires >= {_px(minimum_graph_height)})"
            )
        graph_kind = str(region.get("graphKind") or "missing")
        if graph_kind != "svg":
            failures.append(f"{label}: Brain Atlas {region_label} graph must be one visible SVG")
        else:
            horizontal_fill = _number(region.get("horizontalFillRatio"))
            minimum_fill = KIOSK_LEGIBILITY_THRESHOLDS["atlasHorizontalFillRatio"]
            if horizontal_fill < minimum_fill:
                failures.append(
                    f"{label}: Brain Atlas {region_label} graph uses {horizontal_fill:.0%} of its horizontal map "
                    f"(requires >= {minimum_fill:.0%})"
                )
            if region.get("svgTitlePresent") is not True or region.get("svgDescriptionPresent") is not True:
                failures.append(f"{label}: Brain Atlas {region_label} graph lacks an SVG title or description")
            for key, glyph_label, minimum in (
                ("primaryGlyphHeights", "primary labels", KIOSK_LEGIBILITY_THRESHOLDS["atlasPrimaryGlyphHeight"]),
                ("secondaryGlyphHeights", "secondary labels", KIOSK_LEGIBILITY_THRESHOLDS["atlasSecondaryGlyphHeight"]),
            ):
                heights = region.get(key) if isinstance(region.get(key), list) else []
                if not heights:
                    failures.append(f"{label}: Brain Atlas {region_label} {glyph_label} are missing")
                elif min(_number(value) for value in heights) < minimum:
                    failures.append(f"{label}: Brain Atlas {region_label} {glyph_label} render below {_px(minimum)}")
        if graph_kind == "svg":
            overlap_count = int(_number(region.get("nodeOverlapCount"), missing=-1.0))
            if overlap_count < 0:
                failures.append(f"{label}: Brain Atlas unified node-overlap measurement is missing")
            elif overlap_count:
                failures.append(f"{label}: Brain Atlas unified graph has {overlap_count} overlapping same-layer node pair(s)")
            for key, fit_label in (
                ("htmlTextOverflowCount", "HTML text container"),
                ("svgTextOverflowCount", "SVG node text"),
                ("svgTextOverlapCount", "SVG title/detail pair"),
            ):
                count = int(_number(region.get(key), missing=-1.0))
                if count < 0:
                    failures.append(
                        f"{label}: Brain Atlas unified {fit_label} measurement is missing"
                    )
                elif count:
                    failures.append(
                        f"{label}: Brain Atlas unified has {count} overflowing {fit_label}(s)"
                    )
            layer_heights = region.get("layerGlyphHeights") if isinstance(region.get("layerGlyphHeights"), list) else []
            if not layer_heights or min(_number(value) for value in layer_heights) < 9:
                failures.append(f"{label}: Brain Atlas unified layer labels render below 9px")

    proof_rows = atlas_view.get("proofRows") if isinstance(atlas_view.get("proofRows"), list) else []
    proof_state = str(atlas_view.get("proofState") or "")
    proof_empty_text = str(atlas_view.get("proofEmptyText") or "").strip()
    if proof_state == "ready":
        if not 1 <= len(proof_rows) <= 3:
            failures.append(f"{label}: Brain Atlas renders {len(proof_rows)} exact proof rows (ready requires 1 to 3)")
        if proof_empty_text:
            failures.append(f"{label}: Brain Atlas ready proof state also exposes an empty-state message")
    elif proof_state in {"empty", "unavailable"}:
        if proof_rows:
            failures.append(f"{label}: Brain Atlas {proof_state} proof state still renders exact proof rows")
        if not proof_empty_text:
            failures.append(f"{label}: Brain Atlas {proof_state} proof state lacks an explanatory message")
    else:
        failures.append(f"{label}: Brain Atlas exact proof state is missing or invalid")
    known_agents = {"joshex", "josh2", "jaimes", "jain"}
    known_receipt_statuses = {
        "accepted", "planned", "routed", "active", "verifying", "done",
        "blocked", "error", "cancelled",
    }
    for index, proof_row in enumerate(proof_rows, start=1):
        if not isinstance(proof_row, dict):
            failures.append(f"{label}: Brain Atlas proof row {index} is malformed")
            continue
        agent = str(proof_row.get("agent") or "")
        work_label = str(proof_row.get("workLabel") or "").strip()
        visible_work_label = str(proof_row.get("visibleWorkLabel") or "").strip()
        if agent not in known_agents:
            failures.append(f"{label}: Brain Atlas proof row {index} has an unknown agent")
        if not work_label or len(work_label) > 56:
            failures.append(f"{label}: Brain Atlas proof row {index} lacks a concise work name")
        if proof_row.get("opaqueLabel") is True or re.fullmatch(r"Work\s+[a-f0-9]{8}", work_label, re.I):
            failures.append(f"{label}: Brain Atlas proof row {index} exposes an opaque work identifier")
        if not visible_work_label or re.fullmatch(r"Work\s+[a-f0-9]{8}", visible_work_label, re.I):
            failures.append(f"{label}: Brain Atlas proof row {index} lacks a readable visible work name")
        if internal_text_leaks(work_label) or any(pattern.search(work_label) for pattern in PROOF_WORK_LABEL_UNSAFE_PATTERNS):
            failures.append(f"{label}: Brain Atlas proof row {index} exposes an unsafe work name")
        if not str(proof_row.get("receipt") or "").strip():
            failures.append(f"{label}: Brain Atlas proof row {index} lacks an exact receipt")
        if str(proof_row.get("receiptStatus") or "") not in known_receipt_statuses:
            failures.append(f"{label}: Brain Atlas proof row {index} lacks an exact receipt status")
        if not str(proof_row.get("model") or "").strip():
            failures.append(f"{label}: Brain Atlas proof row {index} lacks a verified model")
        if proof_row.get("routeVerified") is not True:
            failures.append(f"{label}: Brain Atlas proof row {index} lacks a verified route")
        if proof_row.get("declaredAnimated") is not False:
            failures.append(f"{label}: Brain Atlas proof row {index} is not declared static")
        if proof_row.get("clipped") is not False:
            failures.append(f"{label}: Brain Atlas proof row {index} is clipped")

    proof_edges = atlas_view.get("proofEdges") if isinstance(atlas_view.get("proofEdges"), list) else []
    if len(proof_edges) < len(proof_rows):
        failures.append(f"{label}: Brain Atlas proof edge evidence is incomplete")
    for index, proof_edge in enumerate(proof_edges, start=1):
        if not isinstance(proof_edge, dict):
            failures.append(f"{label}: Brain Atlas proof edge {index} is malformed")
            continue
        if proof_edge.get("animated") is True or str(proof_edge.get("animationName") or "none") != "none":
            failures.append(f"{label}: Brain Atlas proof edge {index} is animated")
        if proof_edge.get("memoryFlowClass") is True or proof_edge.get("liveClass") is True:
            failures.append(f"{label}: Brain Atlas proof edge {index} impersonates live memory activity")

    today_jobs = measurements.get("todayJobs") if isinstance(measurements.get("todayJobs"), dict) else {}
    row_count = int(_number(today_jobs.get("rowCount"), missing=-1.0))
    declared_count = int(_number(today_jobs.get("declaredRowCount"), missing=-1.0))
    if row_count <= 0:
        failures.append(f"{label}: Today's Jobs has no rendered occurrence rows")
    if declared_count < 0:
        failures.append(f"{label}: Today's Jobs declared row count is missing")
    elif row_count != declared_count:
        failures.append(
            f"{label}: Today's Jobs renders {row_count} rows but declares {declared_count}; "
            "the shorter viewport must retain the full data set"
        )
    if _number(today_jobs.get("scrollOverflowY"), missing=0.0) <= 1:
        failures.append(f"{label}: Today's Jobs is not using its shorter scroll viewport")
    if today_jobs.get("directChildrenValid") is not True:
        failures.append(f"{label}: Today's Jobs rowgroup contains a non-row timeline child")

    memory = measurements.get("memory") if isinstance(measurements.get("memory"), dict) else {}
    if memory.get("evidenceSource") != "governed-memory-registry":
        failures.append(f"{label}: Brain Atlas memory flow is not registry-verified")
    reduced_motion = memory.get("reducedMotion")
    if reduced_motion is not expect_reduced_motion:
        failures.append(
            f"{label}: prefers-reduced-motion is {reduced_motion!r} "
            f"(requires {expect_reduced_motion!r})"
        )
    flow_state = str(memory.get("flowState") or "")
    if flow_state not in {"live", "idle", "unavailable"}:
        failures.append(f"{label}: Brain Atlas memory flow state is missing or invalid")
    if memory.get("mapAnimated") is True or str(memory.get("mapAnimationName") or "none") != "none":
        failures.append(f"{label}: Brain Atlas map shell uses an expensive paint animation")
    #JAIMES: Idle maps intentionally omit the activity glow; require it only for exact live flow.
    if flow_state == "live" and str(memory.get("mapBoxShadow") or "none") == "none":
        failures.append(f"{label}: Brain Atlas map shell lacks its static activity glow")
    edges = memory.get("edges") if isinstance(memory.get("edges"), list) else []
    if not edges:
        failures.append(f"{label}: Brain Atlas memory flow edges are missing")
    live_edges = [edge for edge in edges if isinstance(edge, dict) and edge.get("live") is True]
    animated_edges = [edge for edge in edges if isinstance(edge, dict) and edge.get("animated") is True]
    for edge in live_edges:
        operation = str(edge.get("operation") or "unknown")
        raw_age_seconds = edge.get("ageSeconds")
        age_seconds = _number(raw_age_seconds)
        if edge.get("evidenceValid") is not True:
            failures.append(f"{label}: live {operation} path lacks an exact observed-at timestamp")
        elif isinstance(raw_age_seconds, bool) or not isinstance(raw_age_seconds, (int, float)) or not math.isfinite(age_seconds):
            failures.append(f"{label}: live {operation} path lacks a numeric evidence age")
        elif age_seconds < -5 or age_seconds > MEMORY_ACTIVITY_MAX_AGE_SECONDS:
            failures.append(
                f"{label}: live {operation} path evidence is {age_seconds:g}s old "
                f"(requires -5s to {MEMORY_ACTIVITY_MAX_AGE_SECONDS:g}s)"
            )
        if _number(edge.get("strokeWidth")) < 4:
            failures.append(f"{label}: live {operation} path is not visually pronounced enough")
        if str(edge.get("strokeLinecap") or "") != "round":
            failures.append(f"{label}: live {operation} path lacks a rounded travel beacon")
        if str(edge.get("filter") or "none") != "none":
            failures.append(f"{label}: live {operation} path uses an expensive SVG filter")
        if str(edge.get("stroke") or "none") in {"none", "transparent", "rgba(0, 0, 0, 0)"}:
            failures.append(f"{label}: live {operation} path lacks a visible evidence stroke")
        if not expect_reduced_motion:
            if str(edge.get("strokeDasharray") or "none") == "none":
                failures.append(f"{label}: live {operation} path lacks a visible moving dash")
    if int(_number(memory.get("animatedInactiveCount"), missing=-1.0)) != 0:
        failures.append(f"{label}: an unevidenced Brain Atlas path is animated")
    if flow_state == "live" and not live_edges:
        failures.append(f"{label}: Brain Atlas reports live activity without an exact live path")
    if flow_state != "live" and live_edges:
        failures.append(f"{label}: Brain Atlas exposes live paths while its state is {flow_state}")

    atlas_agent_nodes = memory.get("atlasAgentNodes") if isinstance(memory.get("atlasAgentNodes"), list) else []
    live_work_agents = memory.get("liveWorkAgents") if isinstance(memory.get("liveWorkAgents"), list) else []
    if len(atlas_agent_nodes) != 4:
        failures.append(f"{label}: Brain Atlas exposes {len(atlas_agent_nodes)} agent presence nodes (requires 4)")
    if len(live_work_agents) != 4:
        failures.append(f"{label}: Live Work exposes {len(live_work_agents)} agent presence cards (requires 4)")
    supported_model_families = {"codex", "antigravity", "ollama", "grok"}
    verified_model_labels = {"GPT", "Gemini", "GLM", "Ollama", "Grok"}
    for card in live_work_agents:
        if not isinstance(card, dict):
            failures.append(f"{label}: Live Work model identity is malformed")
            continue
        agent = str(card.get("agent") or "unknown")
        family = str(card.get("modelFamily") or "")
        model_label = str(card.get("modelLabel") or "")
        if card.get("modelVerified") is True:
            if family not in supported_model_families:
                failures.append(f"{label}: {agent} verified model family is {family or 'missing'}")
            if str(card.get("modelChipFamily") or "") != family or card.get("modelChipVerified") is not True:
                failures.append(f"{label}: {agent} controller model chip disagrees with its verified route")
            if not any(model_label.startswith(expected) for expected in verified_model_labels):
                failures.append(f"{label}: {agent} verified model chip lacks a readable model label")
        else:
            if family != "unverified" or card.get("modelChipVerified") is True:
                failures.append(f"{label}: {agent} unverified route is styled as verified")
            if not model_label.startswith("Unverified model"):
                failures.append(f"{label}: {agent} unverified route lacks an explicit neutral label")
        worker_count = int(_number(card.get("workerCount"), missing=-1.0))
        visible_worker_count = int(_number(card.get("visibleWorkerCount"), missing=-1.0))
        worker_families = card.get("workerFamilies") if isinstance(card.get("workerFamilies"), list) else []
        worker_labels = card.get("workerLabels") if isinstance(card.get("workerLabels"), list) else []
        worker_stale_states = card.get("workerStaleStates") if isinstance(card.get("workerStaleStates"), list) else []
        if worker_count < 0 or visible_worker_count < 0 or visible_worker_count > 3 or visible_worker_count > worker_count:
            failures.append(f"{label}: {agent} worker-model count/overflow contract is invalid")
        if len(worker_families) != visible_worker_count or any(str(value) not in supported_model_families for value in worker_families):
            failures.append(f"{label}: {agent} worker model family is missing or unsupported")
        if len(worker_labels) != visible_worker_count or any(not str(value).startswith("Worker · ") for value in worker_labels):
            failures.append(f"{label}: {agent} worker model icon lacks an accessible label")
        if len(worker_stale_states) != visible_worker_count or any(str(value) not in {"true", "false"} for value in worker_stale_states):
            failures.append(f"{label}: {agent} worker model icon lacks heartbeat freshness state")
        hidden_worker_count = max(0, worker_count - visible_worker_count)
        expected_overflow = f"+{hidden_worker_count}" if hidden_worker_count else ""
        if str(card.get("workerOverflow") or "") != expected_overflow:
            failures.append(f"{label}: {agent} worker overflow indicator is inconsistent")
        if _number(card.get("headerOverflowX"), missing=0.0) > 1 or _number(card.get("headerOverflowY"), missing=0.0) > 1:
            failures.append(f"{label}: {agent} controller/model line overflows")
    atlas_working = sorted(
        str(node.get("agent") or "")
        for node in atlas_agent_nodes
        if isinstance(node, dict) and node.get("working") is True
    )
    board_working = sorted(
        str(node.get("agent") or "")
        for node in live_work_agents
        if isinstance(node, dict) and node.get("working") is True
    )
    if atlas_working != board_working:
        failures.append(
            f"{label}: Brain Atlas working agents {atlas_working} do not match "
            f"Live Work working agents {board_working}"
        )
    working_agent_count = int(_number(memory.get("workingAgentCount"), missing=-1.0))
    if working_agent_count != len(atlas_working):
        failures.append(
            f"{label}: Brain Atlas working-agent count is {working_agent_count} "
            f"but {len(atlas_working)} working nodes are rendered"
        )
    retrieval_edges_by_agent = {
        str(edge.get("agent") or ""): edge
        for edge in edges
        if isinstance(edge, dict) and edge.get("operation") == "retrieval" and edge.get("agent")
    }
    for node in atlas_agent_nodes:
        if not isinstance(node, dict):
            failures.append(f"{label}: Brain Atlas agent presence node is malformed")
            continue
        agent = str(node.get("agent") or "unknown")
        if str(node.get("layer") or "") != "memory":
            failures.append(f"{label}: Brain Atlas {agent} shared agent node is outside the memory layer")
        working = node.get("working") is True
        memory_live = str(node.get("memoryState") or "") == "live"
        if str(node.get("workState") or "") != ("working" if working else "quiet"):
            failures.append(f"{label}: Brain Atlas {agent} work-state label disagrees with its working flag")
        if node.get("workClass") is not working:
            failures.append(f"{label}: Brain Atlas {agent} work-presence class disagrees with Live Work state")
        if node.get("memoryClass") is not memory_live:
            failures.append(f"{label}: Brain Atlas {agent} memory class disagrees with its memory state")
        retrieval_edge = retrieval_edges_by_agent.get(agent)
        if not isinstance(retrieval_edge, dict):
            failures.append(f"{label}: Brain Atlas {agent} retrieval path is missing")
        elif (retrieval_edge.get("live") is True) is not memory_live:
            failures.append(f"{label}: Brain Atlas {agent} memory path motion disagrees with exact retrieval state")
        if node.get("memoryAnimated") is True or str(node.get("memoryAnimationName") or "none") != "none":
            failures.append(f"{label}: Brain Atlas {agent} node shell uses an expensive paint animation")
        if memory_live and node.get("memoryReceiptVisible") is not True:
            failures.append(f"{label}: memory-live Brain Atlas agent {agent} lacks its receipt marker")
        if not memory_live and node.get("memoryReceiptVisible") is True:
            failures.append(f"{label}: memory-quiet Brain Atlas agent {agent} shows a receipt marker")
        if expect_reduced_motion:
            if node.get("animated") is True:
                failures.append(f"{label}: reduced-motion mode still animates Brain Atlas {agent} presence")
        else:
            if working and node.get("workAnimated") is not True:
                failures.append(f"{label}: working Brain Atlas agent {agent} lacks an active presence animation")
            if not working and node.get("workAnimated") is True:
                failures.append(f"{label}: quiet Brain Atlas agent {agent} has an active presence animation")
    if expect_reduced_motion:
        if animated_edges:
            failures.append(f"{label}: reduced-motion mode still animates {len(animated_edges)} Brain Atlas path(s)")
    else:
        animation_mismatches = [
            edge for edge in live_edges
            if str(edge.get("animationName") or "") != "memory-flow-travel"
        ]
        if animation_mismatches:
            failures.append(f"{label}: {len(animation_mismatches)} exact live Brain Atlas path(s) are not animated")
        if len(animated_edges) != len(live_edges):
            failures.append(
                f"{label}: Brain Atlas animation count {len(animated_edges)} does not match "
                f"exact live path count {len(live_edges)}"
            )
    return failures


def validate_kiosk_legibility(measurements: Any) -> list[str]:
    """Validate the permanent 1920x1080 distance-legibility contract."""
    if not isinstance(measurements, dict):
        return [f"{KIOSK_PROBE_LABEL}: legibility measurements are missing"]
    failures = validate_control_tower_layout(measurements, label=KIOSK_PROBE_LABEL)
    page_overflow_x = _number(measurements.get("pageOverflowX"))
    page_overflow_y = _number(measurements.get("pageOverflowY"))
    if page_overflow_x > 2:
        failures.append(f"{KIOSK_PROBE_LABEL}: horizontal page overflow is {_px(page_overflow_x)}")
    if page_overflow_y > 2:
        failures.append(f"{KIOSK_PROBE_LABEL}: vertical page overflow is {_px(page_overflow_y)}")

    live_work = measurements.get("liveWork") if isinstance(measurements.get("liveWork"), dict) else {}
    live_contract = (
        ("objectives", "Live Work objective", KIOSK_LEGIBILITY_THRESHOLDS["liveObjectiveFont"]),
        ("names", "Live Work name", KIOSK_LEGIBILITY_THRESHOLDS["liveNameFont"]),
        ("descriptions", "Live Work description", KIOSK_LEGIBILITY_THRESHOLDS["liveDescriptionFont"]),
        ("secondary", "Live Work secondary text", KIOSK_LEGIBILITY_THRESHOLDS["liveSecondaryFont"]),
    )
    for key, label, minimum in live_contract:
        failures.extend(_font_and_clipping_failures(live_work.get(key), label=label, minimum=minimum))

    finops = measurements.get("finops") if isinstance(measurements.get("finops"), dict) else {}
    if not finops.get("bodyPresent"):
        failures.append(f"{KIOSK_PROBE_LABEL}: FinOps command-grid measurements are missing")
    else:
        bottom_dead_space = _number(finops.get("bodyBottomDeadSpace"))
        bottom_overshoot = _number(finops.get("bodyBottomOvershoot"), missing=0.0)
        maximum_dead_space = KIOSK_LEGIBILITY_THRESHOLDS["finopsBottomDeadSpace"]
        if bottom_dead_space > maximum_dead_space:
            failures.append(
                f"{KIOSK_PROBE_LABEL}: FinOps bottom dead space is {_px(bottom_dead_space)} "
                f"(requires <= {_px(maximum_dead_space)})"
            )
        if bottom_overshoot > 2:
            failures.append(f"{KIOSK_PROBE_LABEL}: FinOps body overshoots its panel by {_px(bottom_overshoot)}")

    wallet_width = _number(finops.get("walletWidth"))
    minimum_wallet_width = KIOSK_LEGIBILITY_THRESHOLDS["finopsWalletWidthMin"]
    maximum_wallet_width = KIOSK_LEGIBILITY_THRESHOLDS["finopsWalletWidthMax"]
    if wallet_width < minimum_wallet_width:
        failures.append(
            f"{KIOSK_PROBE_LABEL}: FinOps wallet width is {_px(wallet_width)} "
            f"(requires >= {_px(minimum_wallet_width)})"
        )
    if wallet_width > maximum_wallet_width:
        failures.append(
            f"{KIOSK_PROBE_LABEL}: FinOps wallet width is {_px(wallet_width)} "
            f"(requires <= {_px(maximum_wallet_width)})"
        )

    for axis in ("panelOverflowX", "panelOverflowY"):
        overflow = _number(finops.get(axis), missing=0.0)
        if overflow > 1:
            failures.append(f"{KIOSK_PROBE_LABEL}: FinOps {axis} is {_px(overflow)}")
    if finops.get("walletActionCount") != 4:
        failures.append(f"{KIOSK_PROBE_LABEL}: FinOps wallet action count is {finops.get('walletActionCount')} (requires 4)")
    if finops.get("visibleDetailFeeds") != 0:
        failures.append(f"{KIOSK_PROBE_LABEL}: FinOps overview exposes transaction/activity detail feeds")
    if finops.get("metricBandCount") != 1 or finops.get("metricCounts") != [5]:
        failures.append(
            f"{KIOSK_PROBE_LABEL}: FinOps metric hierarchy is {finops.get('metricBandCount')} band(s) "
            f"with {finops.get('metricCounts')} cells (requires one compact 5-cell band)"
        )
    if finops.get("providerCount") != 4:
        failures.append(f"{KIOSK_PROBE_LABEL}: FinOps provider count is {finops.get('providerCount')} (requires 4)")

    expected_route_colors = {
        "codex": "#65D1D5",
        "antigravity": "#72D69A",
        "ollama": "#A8ABB3",
        "grok": "#1677FF",
    }
    provider_geometry = finops.get("providerGeometry") if isinstance(finops.get("providerGeometry"), list) else []
    seen_providers: set[str] = set()
    for provider in provider_geometry:
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("provider") or "")
        seen_providers.add(provider_id)
        width = _number(provider.get("width"))
        height = _number(provider.get("height"))
        if width < KIOSK_LEGIBILITY_THRESHOLDS["providerCardWidth"] or height < KIOSK_LEGIBILITY_THRESHOLDS["providerCardHeight"]:
            failures.append(
                f"{KIOSK_PROBE_LABEL}: {provider_id or 'provider'} card is {_px(width)}x{_px(height)} "
                f"(requires >= {_px(KIOSK_LEGIBILITY_THRESHOLDS['providerCardWidth'])}x{_px(KIOSK_LEGIBILITY_THRESHOLDS['providerCardHeight'])})"
            )
        if _number(provider.get("overflowX"), missing=0.0) > 1 or _number(provider.get("overflowY"), missing=0.0) > 1:
            failures.append(f"{KIOSK_PROBE_LABEL}: {provider_id or 'provider'} card content overflows")
        expected_color = expected_route_colors.get(provider_id)
        if expected_color and provider.get("routeColor") != expected_color:
            failures.append(
                f"{KIOSK_PROBE_LABEL}: {provider_id} route color is {provider.get('routeColor')} "
                f"(requires {expected_color})"
            )
    if seen_providers != set(expected_route_colors):
        failures.append(f"{KIOSK_PROBE_LABEL}: FinOps provider identities are incomplete")

    provider_contract = (
        ("providerNames", "FinOps provider name", KIOSK_LEGIBILITY_THRESHOLDS["providerNameFont"]),
        ("providerBodies", "FinOps provider body", KIOSK_LEGIBILITY_THRESHOLDS["providerBodyFont"]),
        ("providerMetadata", "FinOps provider metadata", KIOSK_LEGIBILITY_THRESHOLDS["providerMetadataFont"]),
    )
    for key, label, minimum in provider_contract:
        failures.extend(_font_and_clipping_failures(finops.get(key), label=label, minimum=minimum))

    if not finops.get("ledgerPresent"):
        failures.append(f"{KIOSK_PROBE_LABEL}: FinOps model ledger is missing")
    else:
        ledger_overflow_x = _number(finops.get("ledgerOverflowX"))
        ledger_overflow_y = _number(finops.get("ledgerOverflowY"))
        if ledger_overflow_x > 1:
            failures.append(f"{KIOSK_PROBE_LABEL}: FinOps model ledger horizontal overflow is {_px(ledger_overflow_x)}")
        if ledger_overflow_y > 1:
            failures.append(f"{KIOSK_PROBE_LABEL}: FinOps model ledger vertical overflow is {_px(ledger_overflow_y)}")
        ledger_rows = int(_number(finops.get("ledgerRowCount"), missing=0.0))
        if not 1 <= ledger_rows <= 9:
            failures.append(f"{KIOSK_PROBE_LABEL}: FinOps model ledger renders {ledger_rows} rows (requires 1-9)")
        minimum_row_height = _number(finops.get("ledgerRowMinHeight"))
        if minimum_row_height < KIOSK_LEGIBILITY_THRESHOLDS["ledgerRowHeight"]:
            failures.append(
                f"{KIOSK_PROBE_LABEL}: FinOps model ledger row height is {_px(minimum_row_height)} "
                f"(requires >= {_px(KIOSK_LEGIBILITY_THRESHOLDS['ledgerRowHeight'])})"
            )

    if not finops.get("healthPresent"):
        failures.append(f"{KIOSK_PROBE_LABEL}: FinOps health rail is missing")
    else:
        if finops.get("healthCount") != 4:
            failures.append(f"{KIOSK_PROBE_LABEL}: FinOps health rail has {finops.get('healthCount')} cells (requires 4)")
        health_height = _number(finops.get("healthHeight"))
        if not KIOSK_LEGIBILITY_THRESHOLDS["healthHeightMin"] <= health_height <= KIOSK_LEGIBILITY_THRESHOLDS["healthHeightMax"]:
            failures.append(
                f"{KIOSK_PROBE_LABEL}: FinOps health rail height is {_px(health_height)} "
                f"(requires {_px(KIOSK_LEGIBILITY_THRESHOLDS['healthHeightMin'])}-{_px(KIOSK_LEGIBILITY_THRESHOLDS['healthHeightMax'])})"
            )
        if _number(finops.get("healthOverflowX"), missing=0.0) > 1 or _number(finops.get("healthOverflowY"), missing=0.0) > 1:
            failures.append(f"{KIOSK_PROBE_LABEL}: FinOps health rail content overflows")

    today_jobs = measurements.get("todayJobs") if isinstance(measurements.get("todayJobs"), dict) else {}
    non_green_rows = int(_number(today_jobs.get("nonGreenRowCount"), missing=-1.0))
    non_green_summaries = int(_number(today_jobs.get("nonGreenSummaryCount"), missing=-1.0))
    reason_triggers = int(_number(today_jobs.get("reasonTriggerCount"), missing=-1.0))
    if non_green_rows < 0 or non_green_summaries != 4:
        failures.append(f"{KIOSK_PROBE_LABEL}: Today's Jobs non-green reason targets are incomplete")
    if reason_triggers != non_green_rows + non_green_summaries:
        failures.append(
            f"{KIOSK_PROBE_LABEL}: Today's Jobs exposes {reason_triggers} reason trigger(s) "
            f"for {non_green_rows + non_green_summaries} non-green target(s)"
        )
    if int(_number(today_jobs.get("missingReasonCount"), missing=-1.0)) != 0:
        failures.append(f"{KIOSK_PROBE_LABEL}: Today's Jobs has missing non-green explanations")
    if int(_number(today_jobs.get("objectReasonCount"), missing=-1.0)) != 0:
        failures.append(f"{KIOSK_PROBE_LABEL}: Today's Jobs exposes an invalid object/undefined explanation")
    pending_reason = str(today_jobs.get("pendingSummaryReason") or "").lower()
    if "future work" not in pending_reason or "past occurrences" not in pending_reason:
        failures.append(f"{KIOSK_PROBE_LABEL}: Today's Jobs pending summary does not explain future versus failed")
    if not today_jobs.get("nowMarkerPresent") or "current time" not in str(today_jobs.get("nowMarkerLabel") or "").lower():
        failures.append(f"{KIOSK_PROBE_LABEL}: Today's Jobs current-time marker is missing or unlabeled")
    if today_jobs.get("followNowState") not in {"centered", "all-visible"}:
        failures.append(f"{KIOSK_PROBE_LABEL}: Today's Jobs auto-follow state is {today_jobs.get('followNowState')}")
    if _number(today_jobs.get("scrollOverflowY"), missing=0.0) > 1 and _number(today_jobs.get("nowCenterDelta")) > 32:
        failures.append(
            f"{KIOSK_PROBE_LABEL}: Today's Jobs current-time marker is {_px(_number(today_jobs.get('nowCenterDelta')))} from center"
        )
    if today_jobs.get("directChildrenValid") is not True:
        failures.append(f"{KIOSK_PROBE_LABEL}: Today's Jobs rowgroup contains a non-row timeline child")
    return failures


def playwright_render(url: str, timeout: float, screenshot_path: Path | None) -> tuple[dict[str, Any], list[dict[str, str]], bool]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return row("rendered-react", "skipped", "Playwright unavailable; trying installed Chromium", required=False), [], False

    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []
    bad_responses: list[str] = []
    failures: list[str] = []
    leaks: list[dict[str, str]] = []
    viewport_evidence: list[dict[str, Any]] = []
    screenshot_files: list[Path] = []
    try:
        with sync_playwright() as playwright:
            browser, browser_evidence = launch_playwright_browser(playwright)
            probes = playwright_probe_specs(screenshot_path)
            for label, viewport, probe_screenshot in probes:
                page = browser.new_page(viewport=viewport, device_scale_factor=1)
                page.emulate_media(
                    reduced_motion="reduce" if label == REDUCED_MOTION_PROBE_LABEL else "no-preference"
                )

                def on_console(message: Any, *, probe_label: str = label) -> None:
                    location = message.location or {}
                    location_url = str(location.get("url") or "")
                    if message.type == "error" and not ignorable_browser_request(location_url):
                        safe_message = dashboard_safe_error(RuntimeError(message.text))
                        console_errors.append(f"{probe_label}: {safe_message}"[:300])

                page.on("console", on_console)
                page.on("pageerror", lambda error, probe_label=label: page_errors.append(f"{probe_label}: {dashboard_safe_error(error)}"[:300]))
                page.on(
                    "requestfailed",
                    lambda request, probe_label=label: failed_requests.append(f"{probe_label}: {request.method} {safe_url(request.url)}"[:300])
                    if not ignorable_browser_request(request.url) else None,
                )
                page.on(
                    "response",
                    lambda response, probe_label=label: bad_responses.append(f"{probe_label}: {response.status} {safe_url(response.url)}"[:300])
                    if response.status >= 400 and not ignorable_browser_request(response.url) else None,
                )
                response = page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                page.wait_for_selector("#brain-feed", timeout=int(timeout * 1000))
                page.wait_for_timeout(1250)
                document = page.content()
                probe_failures, probe_leaks, semantics = analyze_rendered_html(document)
                failures.extend(f"{label}: {failure}" for failure in probe_failures)
                for leak in probe_leaks:
                    if leak not in leaks:
                        leaks.append(leak)
                overflow = page.evaluate(
                    """() => {
                      const root = document.documentElement;
                      const viewport = root.clientWidth;
                      const pageOverflow = Math.max(0, root.scrollWidth - viewport);
                      const offenders = [...document.querySelectorAll('body *')]
                        .filter((element) => {
                          const style = getComputedStyle(element);
                          if (style.display === 'none' || style.visibility === 'hidden') return false;
                          const rect = element.getBoundingClientRect();
                          return rect.width > 0 && (rect.right > viewport + 2 || rect.left < -2);
                        })
                        .slice(0, 12)
                        .map((element) => ({
                          tag: element.tagName.toLowerCase(),
                          id: element.id || '',
                          className: String(element.className || '').slice(0, 100),
                          right: Math.round(element.getBoundingClientRect().right),
                        }));
                      return { viewport, pageOverflow, offenders };
                    }"""
                )
                if overflow.get("pageOverflow", 0) > 2:
                    failures.append(f"{label}: horizontal page overflow is {overflow['pageOverflow']}px")
                runtime_measurements = None
                if label in {KIOSK_PROBE_LABEL, REFERENCE_PROBE_LABEL, REDUCED_MOTION_PROBE_LABEL}:
                    runtime_measurements = page.evaluate(KIOSK_LEGIBILITY_EVALUATION)
                    # The full-layout validators supersede the generic
                    # horizontal message and also cover vertical overflow,
                    # initial visibility, panel geometry, jobs, and motion.
                    failures = [failure for failure in failures if failure != f"{label}: horizontal page overflow is {overflow['pageOverflow']}px"]
                    if label == KIOSK_PROBE_LABEL:
                        failures.extend(validate_kiosk_legibility(runtime_measurements))
                    else:
                        failures.extend(validate_control_tower_layout(
                            runtime_measurements,
                            label=label,
                            expect_reduced_motion=label == REDUCED_MOTION_PROBE_LABEL,
                        ))
                probe_evidence = {
                    "name": label,
                    "width": viewport["width"],
                    "height": viewport["height"],
                    "httpStatus": response.status if response else None,
                    "semantics": semantics,
                    "overflow": overflow,
                }
                if runtime_measurements is not None:
                    probe_evidence["runtimeLayout"] = runtime_measurements
                viewport_evidence.append(probe_evidence)
                if probe_screenshot:
                    probe_screenshot.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(probe_screenshot), full_page=True)
                    if probe_screenshot.is_file() and probe_screenshot.stat().st_size > 0:
                        screenshot_files.append(probe_screenshot)
                page.close()
            if console_errors:
                failures.append(f"{len(console_errors)} console error(s)")
            if page_errors:
                failures.append(f"{len(page_errors)} uncaught page error(s)")
            if failed_requests:
                failures.append(f"{len(failed_requests)} failed request(s)")
            if bad_responses:
                failures.append(f"{len(bad_responses)} HTTP error response(s)")
            screenshot_written = bool(screenshot_path and len(screenshot_files) == len(probes))
            browser.close()
    except Exception as exc:  # noqa: BLE001
        return row("rendered-react", "fail", f"Playwright render failed: {dashboard_safe_error(exc)}"), [], False

    evidence = {
        **browser_evidence,
        "viewports": viewport_evidence,
        "consoleErrors": console_errors[:8],
        "pageErrors": page_errors[:8],
        "failedRequests": failed_requests[:8],
        "httpErrorResponses": bad_responses[:8],
        "screenshotsCaptured": len(screenshot_files),
    }
    if failures:
        return row("rendered-react", "fail", "; ".join(failures), evidence=evidence), leaks, screenshot_written
    return row(
        "rendered-react",
        "pass",
        "Desktop, mobile, 1920x1080 kiosk, 2048x1228 reference, and reduced-motion semantics, layout, motion, console, network, overflow, and legibility verified",
        evidence=evidence,
    ), leaks, screenshot_written


def browser_candidates() -> list[str]:
    candidates = [
        os.environ.get("CONTROL_TOWER_BROWSER", ""),
        shutil.which("google-chrome") or "",
        shutil.which("google-chrome-stable") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    return [candidate for index, candidate in enumerate(candidates) if candidate and candidate not in candidates[:index] and Path(candidate).is_file()]


def chromium_render(url: str, timeout: float, screenshot_path: Path | None) -> tuple[dict[str, Any], list[dict[str, str]], bool]:
    candidates = browser_candidates()
    if not candidates:
        return row("rendered-react", "fail", "no Playwright or installed Chromium render engine is available"), [], False
    browser = candidates[0]
    with tempfile.TemporaryDirectory(prefix="control-tower-render-") as profile:
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--use-mock-keychain",
            f"--user-data-dir={profile}",
            "--window-size=1440,1000",
            "--virtual-time-budget=3000",
            "--dump-dom",
        ]
        if screenshot_path:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            command.append(f"--screenshot={screenshot_path}")
        command.append(url)
        process = subprocess.Popen(  # noqa: S603 - fixed local browser command
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        timed_out_after_dom = False
        try:
            stdout, stderr = process.communicate(timeout=max(15, timeout + 8))
        except subprocess.TimeoutExpired as exc:
            # Some macOS Chrome builds emit the complete DOM but retain a
            # background browser process.  Preserve that evidence, then stop
            # the isolated process group rather than reporting a false failure.
            timed_out_after_dom = True
            partial_stdout = exc.stdout or ""
            partial_stderr = exc.stderr or ""
            if isinstance(partial_stdout, bytes):
                partial_stdout = partial_stdout.decode("utf-8", errors="replace")
            if isinstance(partial_stderr, bytes):
                partial_stderr = partial_stderr.decode("utf-8", errors="replace")
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
                stdout, stderr = process.communicate()
            stdout = stdout or partial_stdout
            stderr = stderr or partial_stderr
        except Exception as exc:  # noqa: BLE001
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            return row("rendered-react", "fail", f"Chromium render failed: {dashboard_safe_error(exc)}"), [], False
    if process.returncode not in {0, -signal.SIGTERM, -signal.SIGKILL} or not stdout.strip():
        detail = dashboard_safe_error(RuntimeError(stderr)) if stderr.strip() else "no rendered DOM returned"
        return row("rendered-react", "fail", f"Chromium render failed (rc={process.returncode}): {detail}"), [], False
    failures, leaks, evidence = analyze_rendered_html(stdout)
    evidence.update({
        "engine": Path(browser).name,
        "limitations": ["console", "request failures", "element overflow", "mobile viewport"],
        "processStoppedAfterDom": timed_out_after_dom,
    })
    screenshot_written = bool(screenshot_path and screenshot_path.is_file() and screenshot_path.stat().st_size > 0)
    if failures:
        return row("rendered-react", "fail", "; ".join(failures), evidence=evidence), leaks, screenshot_written
    return row(
        "rendered-react",
        "degraded",
        "React semantics verified with Chromium DOM fallback; console, request, and overflow inspection unavailable",
        evidence=evidence,
    ), leaks, screenshot_written


def check_rendered_react(url: str, timeout: float, screenshot_path: Path | None) -> tuple[dict[str, Any], list[dict[str, str]], bool]:
    result, leaks, screenshot_written = playwright_render(url, timeout, screenshot_path)
    if result["state"] != "skipped":
        return result, leaks, screenshot_written
    return chromium_render(url, timeout, screenshot_path)


def check_physical_screenshot(screenshot_written: bool, strict: bool) -> dict[str, Any]:
    if screenshot_written:
        return row("screenshot", "pass", "browser screenshot evidence captured", required=strict)
    helper = Path.home() / "scripts" / "capture_mission_control_screen.sh"
    if not helper.exists():
        state = "fail" if strict else "skipped"
        return row("screenshot", state, "physical screenshot helper unavailable", required=strict)
    last_text = ""
    for attempt in range(3):
        try:
            proc = subprocess.run([str(helper)], cwd=ROOT, capture_output=True, text=True, timeout=25, check=False)
        except Exception as exc:  # noqa: BLE001
            last_text = dashboard_safe_error(exc)
            break
        output = " ".join((proc.stdout + " " + proc.stderr).split())
        if proc.returncode == 0 and "SCREENSHOT_OK" in output:
            suffix = "" if attempt == 0 else f" after retry {attempt}"
            return row("screenshot", "pass", f"physical kiosk screenshot captured{suffix}", required=strict)
        last_text = dashboard_safe_error(RuntimeError(output)) if output else f"screenshot helper failed rc={proc.returncode}"
        time.sleep(1)
    state = "fail" if strict else "degraded"
    return row("screenshot", state, f"physical screenshot not verified: {last_text}", required=strict)


def summarize(checks: list[dict[str, Any]]) -> tuple[bool, str, list[str], list[str]]:
    failures = [check["detail"] for check in checks if check["state"] == "fail" and check["required"]]
    degraded = [check["detail"] for check in checks if check["state"] in {"degraded", "skipped"}]
    if failures:
        return False, "attention", failures, degraded
    if degraded:
        return True, "degraded", failures, degraded
    return True, "ok", failures, degraded


def self_test() -> int:
    good = """
    <html><body><h1>Josh 2.0 | Control Tower</h1>
    <section aria-label="Control Tower summary"></section>
    <section id="brain-feed" aria-label="Live Work Board">Live Work Board</section>
    <aside id="today-jobs">Today's Jobs</aside>
    <section id="brain-atlas">Brain Atlas</section>
    <section id="finops-dashboard">FinOps Dashboard</section></body></html>
    """
    failures, leaks, _ = analyze_rendered_html(good)
    bad_failures, bad_leaks, _ = analyze_rendered_html(good.replace("Today's Jobs", "/Users/private/token"))
    missing_browser_detected = bundled_playwright_browser_missing(
        RuntimeError("BrowserType.launch: Executable doesn't exist. Please run playwright install.")
    )
    unrelated_failure_rejected = not bundled_playwright_browser_missing(
        RuntimeError("BrowserType.launch: browser process crashed")
    )
    good_kiosk = {
        "pageOverflowX": 0,
        "pageOverflowY": 0,
        "layout": {
            "liveWork": {"fullyInViewport": True},
            "todayJobs": {"fullyInViewport": True},
            "brainAtlas": {"fullyInViewport": True},
            "finops": {"fullyInViewport": True},
            "atlasFinopsTopDelta": 0,
            "atlasFinopsHeightDelta": 0,
            "jobsAboveFinopsGap": 7,
            "liveAboveAtlasGap": 7,
        },
        "memory": {
            "flowState": "live",
            "reducedMotion": False,
            "mapAnimationName": "none",
            "mapAnimated": False,
            "mapBoxShadow": "rgba(88, 238, 154, 0.06) 0px 0px 18px",
            "evidenceSource": "governed-memory-registry",
            "edges": [
                {
                    "agent": "josh2",
                    "operation": "retrieval",
                    "observedAt": "2026-01-01T00:00:00Z",
                    "evidenceValid": True,
                    "ageSeconds": 10,
                    "live": True,
                    "animationName": "memory-flow-travel",
                    "animated": True,
                    "strokeWidth": 4.4,
                    "strokeDasharray": "14px, 9px",
                    "strokeLinecap": "round",
                    "stroke": "rgba(101, 217, 255, 0.96)",
                    "filter": "none",
                },
                {"agent": "joshex", "operation": "retrieval", "observedAt": "", "evidenceValid": False, "ageSeconds": None, "live": False, "animationName": "none", "animated": False},
                {"agent": "jaimes", "operation": "retrieval", "observedAt": "", "evidenceValid": False, "ageSeconds": None, "live": False, "animationName": "none", "animated": False},
                {"agent": "jain", "operation": "retrieval", "observedAt": "", "evidenceValid": False, "ageSeconds": None, "live": False, "animationName": "none", "animated": False},
            ],
            "liveEdgeCount": 1,
            "animatedEdgeCount": 1,
            "animatedInactiveCount": 0,
            "atlasAgentNodes": [
                {"agent": "joshex", "layer": "memory", "working": False, "workState": "quiet", "memoryState": "idle", "workClass": False, "memoryClass": False, "memoryReceiptVisible": False, "auraAnimationName": "none", "presenceAnimationName": "none", "memoryAnimationName": "none", "workAnimated": False, "memoryAnimated": False, "animated": False},
                {"agent": "josh2", "layer": "memory", "working": True, "workState": "working", "memoryState": "live", "workClass": True, "memoryClass": True, "memoryReceiptVisible": True, "auraAnimationName": "memory-agent-presence-halo", "presenceAnimationName": "memory-agent-presence-dot", "memoryAnimationName": "none", "memoryFilter": "none", "memoryStrokeWidth": 3.1, "workAnimated": True, "memoryAnimated": False, "animated": True},
                {"agent": "jaimes", "layer": "memory", "working": False, "workState": "quiet", "memoryState": "idle", "workClass": False, "memoryClass": False, "memoryReceiptVisible": False, "auraAnimationName": "none", "presenceAnimationName": "none", "memoryAnimationName": "none", "workAnimated": False, "memoryAnimated": False, "animated": False},
                {"agent": "jain", "layer": "memory", "working": False, "workState": "quiet", "memoryState": "idle", "workClass": False, "memoryClass": False, "memoryReceiptVisible": False, "auraAnimationName": "none", "presenceAnimationName": "none", "memoryAnimationName": "none", "workAnimated": False, "memoryAnimated": False, "animated": False},
            ],
            "liveWorkAgents": [
                {"agent": "joshex", "working": False, "modelFamily": "unverified", "modelVerified": False, "modelLabel": "Unverified model route pending", "modelChipFamily": "unverified", "modelChipVerified": False, "workerCount": 0, "visibleWorkerCount": 0, "workerFamilies": [], "workerLabels": [], "workerStaleStates": [], "workerOverflow": "", "headerOverflowX": 0, "headerOverflowY": 0},
                {"agent": "josh2", "working": True, "modelFamily": "codex", "modelVerified": True, "modelLabel": "GPT codex/gpt-5.6-terra", "modelChipFamily": "codex", "modelChipVerified": True, "workerCount": 1, "visibleWorkerCount": 1, "workerFamilies": ["ollama"], "workerLabels": ["Worker · GLM · glm-5.2:cloud · active"], "workerStaleStates": ["false"], "workerOverflow": "", "headerOverflowX": 0, "headerOverflowY": 0},
                {"agent": "jaimes", "working": False, "modelFamily": "unverified", "modelVerified": False, "modelLabel": "Unverified model route pending", "modelChipFamily": "unverified", "modelChipVerified": False, "workerCount": 0, "visibleWorkerCount": 0, "workerFamilies": [], "workerLabels": [], "workerStaleStates": [], "workerOverflow": "", "headerOverflowX": 0, "headerOverflowY": 0},
                {"agent": "jain", "working": False, "modelFamily": "unverified", "modelVerified": False, "modelLabel": "Unverified model route pending", "modelChipFamily": "unverified", "modelChipVerified": False, "workerCount": 0, "visibleWorkerCount": 0, "workerFamilies": [], "workerLabels": [], "workerStaleStates": [], "workerOverflow": "", "headerOverflowX": 0, "headerOverflowY": 0},
            ],
            "workingAgentCount": 1,
        },
        "brainAtlasView": {
            "active": "unified",
            "tone": "clear",
            "statusText": "1 working · Memory live · 2 exact receipts",
            "visiblePanelCount": 1,
            "legacyViewControlCount": 0,
            "layerCounts": {"memory": 1, "proof": 0},
            "proofState": "ready",
            "proofAuditVisible": True,
            "proofHealthVisible": True,
            "proofEmptyText": "",
            "proofRows": [
                {"agent": "josh2", "workLabel": "Refresh Control Tower health", "visibleWorkLabel": "Refresh Control Tower health", "receipt": "receipt-1", "receiptStatus": "done", "model": "codex/gpt-5.6-terra", "routeVerified": True, "declaredAnimated": False, "opaqueLabel": False, "clipped": False},
                {"agent": "jaimes", "workLabel": "Verify scheduled agent jobs", "visibleWorkLabel": "Verify scheduled agent jobs", "receipt": "receipt-2", "receiptStatus": "active", "model": "codex/gpt-5.6-sol", "routeVerified": True, "declaredAnimated": False, "opaqueLabel": False, "clipped": False},
            ],
            "proofEdges": [
                {"animationName": "none", "animated": False, "memoryFlowClass": False, "liveClass": False},
                {"animationName": "none", "animated": False, "memoryFlowClass": False, "liveClass": False},
            ],
        },
        "brainAtlasSections": {
            "unified": {
                "contained": True,
                "heading": "Governed memory activity",
                "description": "Shared memory is recalled, applied, assessed, and promoted—not private reasoning.",
                "headingFontSize": 12,
                "descriptionFontSize": 9.5,
                "headingClipped": False,
                "descriptionClipped": False,
                "labelledBy": "brain-atlas-unified-heading",
                "describedBy": "brain-atlas-unified-description",
                "labelledByTargetPresent": True,
                "height": 410,
                "graphHeight": 320,
                "horizontalFillRatio": 0.95,
                "graphKind": "svg",
                "overflowY": 0,
                "svgTitlePresent": True,
                "svgDescriptionPresent": True,
                "primaryGlyphHeights": [8.2],
                "secondaryGlyphHeights": [7.2],
                "layerGlyphHeights": [9.5],
                "nodeOverlapCount": 0,
                "htmlTextOverflowCount": 0,
                "svgTextOverflowCount": 0,
                "svgTextOverlapCount": 0,
            },
        },
        "liveWork": {
            "objectives": [{"fontSize": 24, "clipped": False}],
            "names": [{"fontSize": 17, "clipped": False}],
            "descriptions": [{"fontSize": 12.5, "clipped": False}],
            "secondary": [{"fontSize": 10.5, "clipped": False}],
        },
        "finops": {
            "bodyPresent": True,
            "bodyBottomDeadSpace": 9,
            "bodyBottomOvershoot": 0,
            "walletWidth": 670,
            "panelOverflowX": 0,
            "panelOverflowY": 0,
            "walletActionCount": 4,
            "visibleDetailFeeds": 0,
            "metricBandCount": 1,
            "metricCounts": [5],
            "providerCount": 4,
            "providerGeometry": [
                {"provider": "codex", "width": 199, "height": 124, "overflowX": 0, "overflowY": 0, "routeColor": "#65D1D5"},
                {"provider": "antigravity", "width": 199, "height": 124, "overflowX": 0, "overflowY": 0, "routeColor": "#72D69A"},
                {"provider": "ollama", "width": 199, "height": 124, "overflowX": 0, "overflowY": 0, "routeColor": "#A8ABB3"},
                {"provider": "grok", "width": 199, "height": 124, "overflowX": 0, "overflowY": 0, "routeColor": "#1677FF"},
            ],
            "providerNames": [{"fontSize": 12, "clipped": False}],
            "providerBodies": [{"fontSize": 8, "clipped": False}],
            "providerMetadata": [{"fontSize": 8, "clipped": False}],
            "ledgerPresent": True,
            "ledgerOverflowX": 0,
            "ledgerOverflowY": 0,
            "ledgerRowCount": 9,
            "ledgerRowMinHeight": 23,
            "healthPresent": True,
            "healthCount": 4,
            "healthHeight": 56,
            "healthOverflowX": 0,
            "healthOverflowY": 0,
        },
        "todayJobs": {
            "rowCount": 113,
            "declaredRowCount": 113,
            "nonGreenRowCount": 64,
            "nonGreenSummaryCount": 4,
            "reasonTriggerCount": 68,
            "missingReasonCount": 0,
            "objectReasonCount": 0,
            "pendingSummaryReason": "28 past outcomes lack a terminal receipt. These are past occurrences, never future scheduled work. 23 occurrences are scheduled later today. They are future work, not open or overdue work.",
            "nowMarkerPresent": True,
            "nowMarkerLabel": "Current time, 11:03 AM Eastern Time",
            "scrollOverflowY": 3277,
            "nowCenterDelta": 0,
            "followNowState": "centered",
            "directChildrenValid": True,
        },
    }
    bad_kiosk = json.loads(json.dumps(good_kiosk))
    bad_kiosk["pageOverflowY"] = 8
    bad_kiosk["liveWork"]["objectives"][0]["clipped"] = True
    bad_kiosk["finops"]["walletWidth"] = 720
    bad_kiosk["finops"]["metricCounts"] = [5, 4]
    bad_kiosk["finops"]["providerGeometry"][0]["routeColor"] = "#FFFFFF"
    good_kiosk_failures = validate_kiosk_legibility(good_kiosk)
    bad_kiosk_failures = validate_kiosk_legibility(bad_kiosk)
    ok = (
        not failures
        and not leaks
        and bool(bad_failures)
        and bool(bad_leaks)
        and missing_browser_detected
        and unrelated_failure_rejected
        and not good_kiosk_failures
        and len(bad_kiosk_failures) >= 5
    )
    print(json.dumps({
        "ok": ok,
        "goodFailures": failures,
        "badFailures": bad_failures,
        "badLeaks": bad_leaks,
        "missingBrowserDetected": missing_browser_detected,
        "unrelatedFailureRejected": unrelated_failure_rejected,
        "goodKioskFailures": good_kiosk_failures,
        "badKioskFailures": bad_kiosk_failures,
    }, indent=2))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the rendered Control Tower React kiosk.")
    parser.add_argument("--url", default=DEFAULT_KIOSK_URL)
    parser.add_argument("--data", "--dashboard", dest="data_path", type=Path, help="Generated Control Tower JSON; defaults to slim live data, then dashboard fallback.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--screenshot-path", type=Path)
    parser.add_argument("--no-screenshot", action="store_true")
    parser.add_argument("--strict-visual", action="store_true", help="Treat unavailable screenshot evidence as a failure.")
    parser.add_argument("--strict-browser", action="store_true", help="Require full Playwright console/network/overflow inspection.")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    parsed_url = urllib.parse.urlsplit(args.url)
    if parsed_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("--url must target the loopback Control Tower kiosk")
    if args.screenshot_path and args.screenshot_path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        parser.error("--screenshot-path must end in .png, .jpg, or .jpeg")

    checked_at = utc_now()
    data_path = args.data_path or (LIVE_DATA if LIVE_DATA.exists() else DASHBOARD_FALLBACK)
    checks = [check_http(args.url, args.timeout), check_control_tower_json(data_path)]
    render_check, leaks, screenshot_written = check_rendered_react(args.url, args.timeout, args.screenshot_path)
    if args.strict_browser and render_check["state"] == "degraded":
        render_check = row(
            "rendered-react",
            "fail",
            f"strict browser evidence required: {render_check['detail']}",
            evidence=render_check.get("evidence"),
        )
    checks.append(render_check)
    if args.no_screenshot:
        checks.append(row("screenshot", "skipped", "screenshot explicitly disabled", required=False))
    else:
        checks.append(check_physical_screenshot(screenshot_written, args.strict_visual))

    ok, status, issues, degraded = summarize(checks)
    if ok and status == "ok":
        summary = "Rendered Control Tower semantics, console, network, layout, data, and screenshot verified."
    elif ok:
        summary = "Required Control Tower checks passed with explicitly degraded visual/runtime evidence."
    else:
        summary = "; ".join(issues)
    payload = {
        "ok": ok,
        "status": status,
        "checkedAt": checked_at,
        "url": safe_url(args.url),
        "summary": summary,
        "issues": issues,
        "degradedReasons": degraded,
        "checks": checks,
        "textQuality": {"visibleInternalTextLeaks": leaks},
        "source": "mission_control_runtime_layout_check.py",
    }
    atomic_write(args.output, payload)
    print(json.dumps(payload, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
