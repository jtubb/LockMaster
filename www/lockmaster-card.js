class LockMasterCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._selectedUserId = null;
    this._result = null;
    this._loading = false;
    this._showPin = false;
    // Form state - persists across re-renders
    this._formState = {
      name: '',
      email: '',
      pin: '',
    };
    this._formDirty = false;
    this._interacting = false;
    this._interactionTimeout = null;
  }

  set hass(hass) {
    this._hass = hass;
    // Only re-render if we're not in the middle of editing or interacting
    if (!this._formDirty && !this._interacting) {
      this._render();
    } else {
      // Just update the locks info without touching form
      this._updateLocksInfo();
    }
  }

  _startInteraction() {
    this._interacting = true;
    // Clear any existing timeout
    if (this._interactionTimeout) {
      clearTimeout(this._interactionTimeout);
    }
    // Auto-clear after 10 seconds as a safety net
    this._interactionTimeout = setTimeout(() => {
      this._interacting = false;
    }, 10000);
  }

  _endInteraction() {
    if (this._interactionTimeout) {
      clearTimeout(this._interactionTimeout);
      this._interactionTimeout = null;
    }
    this._interacting = false;
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error('Please define an entity (e.g., sensor.lockmaster_user_list)');
    }
    this._config = config;
  }

  getCardSize() {
    return 5;
  }

  static getConfigElement() {
    return document.createElement('lockmaster-card-editor');
  }

  static getStubConfig() {
    return { entity: 'sensor.lockmaster_user_list' };
  }

  _getUsers() {
    if (!this._hass || !this._config) return [];

    const entity = this._hass.states[this._config.entity];
    if (!entity || !entity.attributes) return [];

    const enabledUsers = entity.attributes.enabled_users || [];
    const availableSlots = entity.attributes.available_slots || [];

    // Combine and sort by ID
    const allUsers = [...enabledUsers, ...availableSlots];
    allUsers.sort((a, b) => parseInt(a.id) - parseInt(b.id));

    return allUsers;
  }

  _getSelectedUser() {
    if (!this._selectedUserId) return null;
    const users = this._getUsers();
    return users.find(u => u.id === this._selectedUserId);
  }

  _loadUserIntoForm(user) {
    if (user) {
      this._formState = {
        name: user.name || '',
        email: user.email || '',
        pin: '',
      };
    } else {
      this._formState = { name: '', email: '', pin: '' };
    }
    this._formDirty = false;
  }

  _updateLocksInfo() {
    const locksInfo = this.shadowRoot?.querySelector('.locks-info');
    if (locksInfo && this._hass && this._config) {
      const entity = this._hass.states[this._config.entity];
      const locks = entity?.attributes?.locks || [];
      locksInfo.innerHTML = `<strong>Managing:</strong> ${locks.join(', ') || 'No locks configured'}`;
    }
  }

  _handleInputChange(field, value) {
    this._formState[field] = value;
    this._formDirty = true;
  }

  async _updateUser() {
    if (!this._formState.name || !this._formState.email) {
      this._result = { success: false, status: 'Name and email are required' };
      this._render();
      return;
    }

    // Get current user's pin if no new pin entered
    const selectedUser = this._getSelectedUser();
    const pinToUse = this._formState.pin || selectedUser?.pin || '';

    if (!pinToUse || pinToUse === '000') {
      this._result = { success: false, status: 'A valid PIN is required' };
      this._render();
      return;
    }

    this._loading = true;
    this._result = null;
    this._formDirty = false;
    this._render();

    try {
      const result = await this._hass.callService('lockmaster', 'update_user', {
        user_id: this._selectedUserId,
        name: this._formState.name,
        email: this._formState.email,
        pin: pinToUse,
      }, undefined, true, true);

      this._result = result?.response || { success: true, status: 'User updated' };
    } catch (error) {
      this._result = { success: false, status: error.message || 'Failed to update user' };
    }

    this._loading = false;
    this._render();
  }

  async _disableUser() {
    if (!this._selectedUserId || this._selectedUserId === '0') {
      this._result = { success: false, status: 'Cannot disable Master user' };
      this._render();
      return;
    }

    this._loading = true;
    this._result = null;
    this._formDirty = false;
    this._render();

    try {
      const result = await this._hass.callService('lockmaster', 'disable_user', {
        user_id: this._selectedUserId,
      }, undefined, true, true);

      this._result = result?.response || { success: true, status: 'User disabled' };
      // Reset selection after disable
      this._selectedUserId = null;
      this._loadUserIntoForm(null);
    } catch (error) {
      this._result = { success: false, status: error.message || 'Failed to disable user' };
    }

    this._loading = false;
    this._render();
  }

  _handleUserChange(e) {
    this._selectedUserId = e.target.value || null;
    this._result = null;
    this._showPin = false;
    this._endInteraction();
    const user = this._getSelectedUser();
    this._loadUserIntoForm(user);
    this._render();
  }

  _toggleShowPin() {
    this._showPin = !this._showPin;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;

    const users = this._getUsers();
    const selectedUser = this._getSelectedUser();
    const entity = this._hass.states[this._config.entity];
    const locks = entity?.attributes?.locks || [];

    // Get current PIN for display
    const currentPin = selectedUser?.pin || '000';
    const hasValidPin = currentPin && currentPin !== '000';

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
        }
        ha-card {
          padding: 16px;
        }
        .card-header {
          font-size: 1.2em;
          font-weight: 500;
          padding-bottom: 12px;
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .card-header ha-icon {
          color: var(--primary-color);
        }
        .locks-info {
          font-size: 0.85em;
          color: var(--secondary-text-color);
          margin-bottom: 16px;
          padding: 8px 12px;
          background: var(--secondary-background-color);
          border-radius: 8px;
        }
        .form-group {
          margin-bottom: 16px;
        }
        .form-group label {
          display: block;
          font-size: 0.9em;
          font-weight: 500;
          margin-bottom: 6px;
          color: var(--primary-text-color);
        }
        select, input[type="text"], input[type="email"] {
          width: 100%;
          padding: 10px 12px;
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          background: var(--card-background-color);
          color: var(--primary-text-color);
          font-size: 1em;
          box-sizing: border-box;
        }
        select:focus, input:focus {
          outline: none;
          border-color: var(--primary-color);
          box-shadow: 0 0 0 1px var(--primary-color);
        }
        input:disabled {
          background: var(--disabled-color, #eee);
          color: var(--disabled-text-color, #999);
        }
        .user-status {
          display: inline-block;
          padding: 2px 8px;
          border-radius: 12px;
          font-size: 0.8em;
          margin-left: 8px;
        }
        .status-enabled {
          background: var(--success-color, #4caf50);
          color: white;
        }
        .status-available {
          background: var(--warning-color, #ff9800);
          color: white;
        }
        .button-group {
          display: flex;
          gap: 12px;
          margin-top: 20px;
        }
        button {
          flex: 1;
          padding: 12px 16px;
          border: none;
          border-radius: 8px;
          font-size: 1em;
          font-weight: 500;
          cursor: pointer;
          transition: opacity 0.2s, transform 0.1s;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
        }
        button:hover {
          opacity: 0.9;
        }
        button:active {
          transform: scale(0.98);
        }
        button:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        .btn-update {
          background: var(--primary-color);
          color: var(--text-primary-color, white);
        }
        .btn-delete {
          background: var(--error-color, #f44336);
          color: white;
        }
        .result {
          margin-top: 16px;
          padding: 12px;
          border-radius: 8px;
          font-size: 0.95em;
        }
        .result-success {
          background: var(--success-color, #4caf50);
          color: white;
        }
        .result-error {
          background: var(--error-color, #f44336);
          color: white;
        }
        .loading {
          text-align: center;
          padding: 20px;
          color: var(--secondary-text-color);
        }
        .no-selection {
          text-align: center;
          padding: 40px 20px;
          color: var(--secondary-text-color);
        }
        .user-id-badge {
          display: inline-block;
          background: var(--primary-color);
          color: white;
          padding: 2px 8px;
          border-radius: 4px;
          font-size: 0.8em;
          margin-right: 8px;
        }
        .pin-group {
          display: flex;
          gap: 8px;
          align-items: stretch;
        }
        .pin-group input {
          flex: 1;
        }
        .btn-show-pin {
          padding: 10px 14px;
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          background: var(--secondary-background-color);
          color: var(--primary-text-color);
          cursor: pointer;
          font-size: 0.9em;
          white-space: nowrap;
        }
        .btn-show-pin:hover {
          background: var(--primary-color);
          color: white;
          border-color: var(--primary-color);
        }
        .current-pin {
          margin-top: 8px;
          padding: 8px 12px;
          background: var(--secondary-background-color);
          border-radius: 6px;
          font-family: monospace;
          font-size: 1.1em;
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .current-pin .pin-value {
          font-weight: bold;
          letter-spacing: 2px;
        }
        .current-pin .pin-label {
          color: var(--secondary-text-color);
          font-family: inherit;
          font-size: 0.85em;
        }
        .no-pin {
          color: var(--warning-color, #ff9800);
          font-style: italic;
        }
      </style>

      <ha-card>
        <div class="card-header">
          <ha-icon icon="mdi:account-key"></ha-icon>
          ${this._config.title || 'LockMaster User Management'}
        </div>

        <div class="locks-info">
          <strong>Managing:</strong> ${locks.join(', ') || 'No locks configured'}
        </div>

        <div class="form-group">
          <label>Select User</label>
          <select id="user-select">
            <option value="">-- Select a user --</option>
            ${users.map(user => `
              <option value="${user.id}" ${this._selectedUserId === user.id ? 'selected' : ''}>
                ${user.name} (${user.status})
              </option>
            `).join('')}
          </select>
        </div>

        ${this._loading ? `
          <div class="loading">
            Processing...
          </div>
        ` : selectedUser ? `
          <div class="form-group">
            <label>
              <span class="user-id-badge">ID: ${selectedUser.id}</span>
              Name
              <span class="user-status status-${selectedUser.status}">${selectedUser.status}</span>
            </label>
            <input
              type="text"
              id="user-name"
              value="${this._formState.name}"
              ${selectedUser.id === '0' ? 'disabled' : ''}
              placeholder="Enter user name"
            />
          </div>

          <div class="form-group">
            <label>Email</label>
            <input
              type="email"
              id="user-email"
              value="${this._formState.email}"
              placeholder="Enter email address"
            />
          </div>

          <div class="form-group">
            <label>PIN (4-8 digits) - Leave blank to keep current PIN</label>
            <div class="pin-group">
              <input
                type="text"
                id="user-pin"
                value="${this._formState.pin}"
                placeholder="Enter new PIN to change"
                pattern="[0-9]{4,8}"
                inputmode="numeric"
              />
              <button type="button" class="btn-show-pin" id="btn-show-pin">
                ${this._showPin ? 'Hide' : 'Show'} PIN
              </button>
            </div>
            ${this._showPin ? `
              <div class="current-pin">
                <span class="pin-label">Current PIN:</span>
                ${hasValidPin ? `
                  <span class="pin-value">${currentPin}</span>
                ` : `
                  <span class="no-pin">No PIN set</span>
                `}
              </div>
            ` : ''}
          </div>

          <div class="button-group">
            <button class="btn-update" id="btn-update">
              Update User
            </button>
            <button
              class="btn-delete"
              id="btn-delete"
              ${selectedUser.id === '0' ? 'disabled' : ''}
              title="${selectedUser.id === '0' ? 'Cannot disable Master user' : 'Disable this user'}"
            >
              Disable User
            </button>
          </div>
        ` : `
          <div class="no-selection">
            <p>Select a user from the dropdown to view and edit their details.</p>
          </div>
        `}

        ${this._result ? `
          <div class="result ${this._result.success ? 'result-success' : 'result-error'}">
            <strong>${this._result.success ? 'Success' : 'Error'}:</strong>
            ${this._result.status || (this._result.success ? 'Operation completed' : 'Operation failed')}
          </div>
        ` : ''}
      </ha-card>
    `;

    // Add event listeners after rendering
    this._attachEventListeners();
  }

  _attachEventListeners() {
    const select = this.shadowRoot.getElementById('user-select');
    if (select) {
      // Start interaction on any mouse/touch to prevent re-renders while dropdown is open
      select.addEventListener('mousedown', () => this._startInteraction());
      select.addEventListener('touchstart', () => this._startInteraction());
      select.addEventListener('focus', () => this._startInteraction());
      select.addEventListener('change', (e) => this._handleUserChange(e));
      // End interaction if user clicks away without selecting
      select.addEventListener('blur', () => {
        // Small delay to allow change event to fire first
        setTimeout(() => this._endInteraction(), 100);
      });
    }

    const nameInput = this.shadowRoot.getElementById('user-name');
    if (nameInput) {
      nameInput.addEventListener('input', (e) => this._handleInputChange('name', e.target.value));
    }

    const emailInput = this.shadowRoot.getElementById('user-email');
    if (emailInput) {
      emailInput.addEventListener('input', (e) => this._handleInputChange('email', e.target.value));
    }

    const pinInput = this.shadowRoot.getElementById('user-pin');
    if (pinInput) {
      pinInput.addEventListener('input', (e) => this._handleInputChange('pin', e.target.value));
    }

    const updateBtn = this.shadowRoot.getElementById('btn-update');
    if (updateBtn) {
      updateBtn.addEventListener('click', () => this._updateUser());
    }

    const deleteBtn = this.shadowRoot.getElementById('btn-delete');
    if (deleteBtn) {
      deleteBtn.addEventListener('click', () => this._disableUser());
    }

    const showPinBtn = this.shadowRoot.getElementById('btn-show-pin');
    if (showPinBtn) {
      showPinBtn.addEventListener('click', () => this._toggleShowPin());
    }
  }
}

// Card Editor
class LockMasterCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }

  set hass(hass) {
    this._hass = hass;
  }

  setConfig(config) {
    this._config = config;
    this._render();
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        .form-group {
          margin-bottom: 16px;
        }
        label {
          display: block;
          margin-bottom: 4px;
          font-weight: 500;
        }
        input {
          width: 100%;
          padding: 8px;
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          box-sizing: border-box;
        }
      </style>

      <div class="form-group">
        <label>Entity (LockMaster User List sensor)</label>
        <input
          type="text"
          id="entity"
          value="${this._config?.entity || 'sensor.lockmaster_user_list'}"
          placeholder="sensor.lockmaster_user_list"
        />
      </div>

      <div class="form-group">
        <label>Title (optional)</label>
        <input
          type="text"
          id="title"
          value="${this._config?.title || ''}"
          placeholder="LockMaster User Management"
        />
      </div>
    `;

    this.shadowRoot.getElementById('entity').addEventListener('change', (e) => {
      this._config = { ...this._config, entity: e.target.value };
      this._fireConfigChanged();
    });

    this.shadowRoot.getElementById('title').addEventListener('change', (e) => {
      this._config = { ...this._config, title: e.target.value };
      this._fireConfigChanged();
    });
  }

  _fireConfigChanged() {
    const event = new CustomEvent('config-changed', {
      detail: { config: this._config },
      bubbles: true,
      composed: true,
    });
    this.dispatchEvent(event);
  }
}

customElements.define('lockmaster-card', LockMasterCard);
customElements.define('lockmaster-card-editor', LockMasterCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'lockmaster-card',
  name: 'LockMaster Card',
  description: 'Manage LockMaster users with a user-friendly interface',
  preview: true,
});

console.info('%c LOCKMASTER-CARD %c 1.4.0 ',
  'background: #4caf50; color: white; font-weight: bold;',
  'background: #ddd; color: #333;'
);
