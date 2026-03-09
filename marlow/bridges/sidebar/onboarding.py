"""Marlow onboarding wizard — first-boot experience.

Runs inside the sidebar on first boot (no config.toml or empty user.name).
Collects: user name, API key, optional Telegram setup.

/ Wizard de onboarding — experiencia de primer arranque.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("marlow.bridges.sidebar.onboarding")


# ─────────────────────────────────────────────────────────────
# Onboarding HTML
# ─────────────────────────────────────────────────────────────

ONBOARDING_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'Inter', system-ui, sans-serif;
    background: #1a1a2e;
    color: #e0e0e0;
    height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 24px;
}

.wizard-card {
    width: 100%;
    max-width: 340px;
    display: none;
}
.wizard-card.active { display: block; }

h2 {
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 16px;
    color: #c4c4e0;
}

p {
    font-size: 13px;
    line-height: 1.6;
    margin-bottom: 16px;
    color: #a0a0c0;
}

.input-group {
    margin-bottom: 16px;
}

input[type="text"], input[type="password"] {
    width: 100%;
    background: #16213e;
    border: 1px solid #2a2a4a;
    border-radius: 8px;
    padding: 10px 14px;
    color: #e0e0e0;
    font-size: 14px;
    outline: none;
}
input:focus { border-color: #4dabf7; }

.btn {
    padding: 10px 20px;
    border: none;
    border-radius: 8px;
    font-size: 13px;
    cursor: pointer;
    transition: background 0.2s;
}
.btn-primary {
    background: #4dabf7;
    color: white;
}
.btn-primary:hover { background: #339af0; }
.btn-secondary {
    background: #2a2a4a;
    color: #a0a0c0;
}
.btn-secondary:hover { background: #3a3a5a; }

.btn-row {
    display: flex;
    gap: 10px;
    margin-top: 20px;
}

.error-msg {
    color: #ff6b6b;
    font-size: 12px;
    margin-top: 8px;
    display: none;
}

.success-msg {
    color: #51cf66;
    font-size: 12px;
    margin-top: 8px;
    display: none;
}

.feature-item {
    display: flex;
    gap: 10px;
    margin-bottom: 12px;
    font-size: 13px;
    color: #c0c0e0;
}
.feature-icon {
    font-size: 18px;
    min-width: 24px;
}

.step-dots {
    display: flex;
    gap: 6px;
    margin-bottom: 24px;
}
.step-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #2a2a4a;
}
.step-dot.active { background: #4dabf7; }
.step-dot.done { background: #51cf66; }
</style>
</head>
<body>

<div class="step-dots" id="step-dots">
    <div class="step-dot active"></div>
    <div class="step-dot"></div>
    <div class="step-dot"></div>
    <div class="step-dot"></div>
</div>

<!-- Step 1: Welcome + Name -->
<div class="wizard-card active" id="step1">
    <h2>Hola, soy Marlow.</h2>
    <p>Voy a ser tu asistente de escritorio. Primero, necesito conocerte.</p>
    <div class="input-group">
        <input type="text" id="user-name" placeholder="Tu nombre"
               onkeydown="if(event.key==='Enter')goStep2()">
    </div>
    <div class="btn-row">
        <button class="btn btn-primary" onclick="goStep2()">Continuar</button>
    </div>
    <div class="error-msg" id="name-error">Por favor ingresa tu nombre</div>
</div>

<!-- Step 2: API Key -->
<div class="wizard-card" id="step2">
    <h2>API Key</h2>
    <p>Para poder ayudarte necesito una API key de Anthropic. Puedes obtenerla en console.anthropic.com</p>
    <div class="input-group">
        <input type="password" id="api-key" placeholder="sk-ant-...">
    </div>
    <div class="error-msg" id="key-error">La API key no es valida</div>
    <div class="success-msg" id="key-success">API key validada</div>
    <div class="btn-row">
        <button class="btn btn-secondary" onclick="skipKey()">Saltar por ahora</button>
        <button class="btn btn-primary" onclick="validateKey()">Continuar</button>
    </div>
</div>

<!-- Step 3: Telegram -->
<div class="wizard-card" id="step3">
    <h2>Telegram</h2>
    <p>Puedes comunicarte conmigo por Telegram desde tu celular. Esto es opcional.</p>
    <div class="btn-row">
        <button class="btn btn-secondary" onclick="skipTelegram()">No, gracias</button>
        <button class="btn btn-primary" onclick="setupTelegram()">Configurar</button>
    </div>
</div>

<!-- Step 3b: Telegram token -->
<div class="wizard-card" id="step3b">
    <h2>Telegram Bot</h2>
    <p>1. Abre Telegram<br>2. Busca @BotFather<br>3. Envia /newbot<br>4. Copia el token aqui:</p>
    <div class="input-group">
        <input type="text" id="tg-token" placeholder="123456:ABC-DEF...">
    </div>
    <div class="btn-row">
        <button class="btn btn-secondary" onclick="skipTelegram()">Saltar</button>
        <button class="btn btn-primary" onclick="saveTelegram()">Continuar</button>
    </div>
</div>

<!-- Step 4: Done -->
<div class="wizard-card" id="step4">
    <h2 id="done-title">Todo listo!</h2>
    <p>Puedes hablarme de 3 formas:</p>
    <div class="feature-item">
        <span class="feature-icon">&#x1F3A4;</span>
        <span>Di "Marlow" o presiona Super+V para hablar</span>
    </div>
    <div class="feature-item">
        <span class="feature-icon">&#x2328;</span>
        <span>Escribe aqui arriba en el sidebar</span>
    </div>
    <div class="feature-item">
        <span class="feature-icon">&#x1F4F1;</span>
        <span id="tg-feature">Envia un mensaje por Telegram</span>
    </div>
    <p style="margin-top: 16px; color: #c4c4e0;">En que te puedo ayudar?</p>
    <div class="btn-row">
        <button class="btn btn-primary" onclick="finishOnboarding()">Empezar</button>
    </div>
</div>

<script>
let currentStep = 1;
let userData = { name: '', apiKey: '', telegramToken: '' };

function updateDots() {
    const dots = document.querySelectorAll('.step-dot');
    dots.forEach((dot, i) => {
        dot.className = 'step-dot';
        if (i + 1 === currentStep) dot.className = 'step-dot active';
        else if (i + 1 < currentStep) dot.className = 'step-dot done';
    });
}

function showStep(n) {
    document.querySelectorAll('.wizard-card').forEach(c => c.classList.remove('active'));
    const step = document.getElementById('step' + n);
    if (step) step.classList.add('active');
    currentStep = n;
    updateDots();
}

function goStep2() {
    const name = document.getElementById('user-name').value.trim();
    if (!name) {
        document.getElementById('name-error').style.display = 'block';
        return;
    }
    document.getElementById('name-error').style.display = 'none';
    userData.name = name;
    document.title = 'ONBOARD:name:' + name;
    showStep(2);
}

function validateKey() {
    const key = document.getElementById('api-key').value.trim();
    if (!key || !key.startsWith('sk-')) {
        document.getElementById('key-error').style.display = 'block';
        document.getElementById('key-success').style.display = 'none';
        return;
    }
    document.getElementById('key-error').style.display = 'none';
    document.getElementById('key-success').style.display = 'block';
    userData.apiKey = key;
    document.title = 'ONBOARD:apikey:' + key;
    setTimeout(() => showStep(3), 800);
}

function skipKey() {
    showStep(3);
}

function setupTelegram() {
    showStep('3b');
}

function skipTelegram() {
    showStep(4);
    document.getElementById('tg-feature').style.display = 'none';
}

function saveTelegram() {
    const token = document.getElementById('tg-token').value.trim();
    if (token) {
        userData.telegramToken = token;
        document.title = 'ONBOARD:telegram:' + token;
    }
    showStep(4);
}

function finishOnboarding() {
    document.title = 'ONBOARD:done:' + JSON.stringify(userData);
}

// Set done title with name
function setDoneTitle() {
    if (userData.name) {
        document.getElementById('done-title').textContent = 'Todo listo, ' + userData.name + '!';
    }
}

// Watch for step 4 to update title
const observer = new MutationObserver(() => {
    if (document.getElementById('step4').classList.contains('active')) {
        setDoneTitle();
    }
});
observer.observe(document.getElementById('step4'), { attributes: true });
</script>
</body>
</html>"""


def get_onboarding_html() -> str:
    """Return the onboarding HTML."""
    return ONBOARDING_HTML


def is_onboarding_needed() -> bool:
    """Check if onboarding is needed (no user name configured)."""
    try:
        from marlow.core.settings import get_settings
        return not get_settings().is_onboarded
    except Exception:
        return True


def process_onboarding_event(event_type: str, value: str):
    """Process an onboarding event from the sidebar.

    Called when the sidebar title changes with ONBOARD: prefix.
    """
    from marlow.core.settings import (
        get_settings, save_settings, save_secret, update_setting,
    )
    from marlow.platform.linux.tts import regenerate_clips

    if event_type == "name":
        update_setting("user", "name", value)
        regenerate_clips(value)
        logger.info("Onboarding: name set to '%s'", value)

    elif event_type == "apikey":
        save_secret("anthropic_api_key", value)
        # Also set env var for current session
        os.environ["ANTHROPIC_API_KEY"] = value
        logger.info("Onboarding: API key saved")

    elif event_type == "telegram":
        save_secret("telegram_bot_token", value)
        update_setting("telegram", "enabled", True)
        logger.info("Onboarding: Telegram token saved")

    elif event_type == "done":
        logger.info("Onboarding complete")
