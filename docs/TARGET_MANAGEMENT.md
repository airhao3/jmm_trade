# Target Address Management

Dynamic management of tracked wallet addresses using CLI commands and external JSON file.

## Overview

Target addresses are stored in `config/targets.json` and can be managed dynamically without editing configuration files. The bot automatically loads enabled targets from this file on startup.

## File Structure

**`config/targets.json`**:
```json
{
  "targets": [
    {
      "address": "0x88f46b9e5d86b4fb85be55ab0ec4004264b9d4db",
      "nickname": "PBot1",
      "enabled": true,
      "added_at": "2026-02-22T03:47:00Z",
      "notes": "Primary target - high volume trader"
    }
  ]
}
```

## CLI Commands

### List Targets

```bash
# List enabled targets only
python main.py list-targets

# List all targets (including disabled)
python main.py list-targets --all

# Output as JSON
python main.py list-targets --json
```

### Add Target

```bash
# Add a new target
python main.py add-target 0xADDRESS NICKNAME

# Add with notes
python main.py add-target 0xADDRESS NICKNAME --notes "Description"

# Add in disabled state
python main.py add-target 0xADDRESS NICKNAME --disabled
```

**Example**:
```bash
python main.py add-target 0x1234567890123456789012345678901234567890 TopTrader --notes "High volume BTC trader"
```

### Remove Target

```bash
# Remove target (with confirmation)
python main.py remove-target IDENTIFIER

# Remove without confirmation
python main.py remove-target IDENTIFIER -y
```

**IDENTIFIER** can be:
- Ethereum address: `0x1234...`
- Nickname: `TopTrader`

**Example**:
```bash
python main.py remove-target TopTrader -y
```

### Enable/Disable Target

```bash
# Enable a target
python main.py enable-target IDENTIFIER

# Disable a target
python main.py disable-target IDENTIFIER
```

**Example**:
```bash
python main.py disable-target PBot1
python main.py enable-target PBot1
```

### Update Target

```bash
# Update nickname
python main.py update-target IDENTIFIER --nickname "NewName"

# Update notes
python main.py update-target IDENTIFIER --notes "New description"

# Update both
python main.py update-target IDENTIFIER --nickname "NewName" --notes "New description"
```

**Example**:
```bash
python main.py update-target PBot1 --notes "Updated: Primary BTC/ETH trader"
```

## Workflow

### Adding a New Target

1. **Find the wallet address** on Polymarket
2. **Add the target**:
   ```bash
   python main.py add-target 0xADDRESS NICKNAME --notes "Description"
   ```
3. **Verify it was added**:
   ```bash
   python main.py list-targets
   ```
4. **Restart the bot** to start tracking:
   ```bash
   # On VPS
   sudo systemctl restart polymarket-bot
   ```

### Temporarily Disabling a Target

```bash
# Disable without removing
python main.py disable-target NICKNAME

# Re-enable later
python main.py enable-target NICKNAME
```

### Removing a Target

```bash
# Remove permanently
python main.py remove-target NICKNAME -y
```

## Integration with Bot

The bot automatically:
1. Loads `config/targets.json` on startup
2. Only tracks **enabled** targets
3. Ignores disabled targets
4. Falls back to `config.yaml` if `targets.json` doesn't exist

**Priority**: `config/targets.json` > `config.yaml`

## Address Validation

- Must be valid Ethereum address (0x + 40 hex characters)
- Automatically converted to lowercase
- Duplicate addresses are rejected

## Best Practices

1. **Use descriptive nicknames**: `PBot1`, `TopBTCTrader`, `WhaleWallet`
2. **Add notes**: Document why you're tracking this address
3. **Disable instead of remove**: Keep history by disabling temporarily
4. **Backup targets.json**: Keep a backup before major changes
5. **Test first**: Add with `--disabled`, verify, then enable

## Examples

### Complete Workflow

```bash
# 1. List current targets
python main.py list-targets

# 2. Add new target
python main.py add-target 0xABCD...1234 WhaleTrader --notes "Large BTC positions"

# 3. Verify
python main.py list-targets

# 4. Test by disabling temporarily
python main.py disable-target WhaleTrader

# 5. Re-enable when ready
python main.py enable-target WhaleTrader

# 6. Restart bot to apply changes
sudo systemctl restart polymarket-bot
```

### Managing Multiple Targets

```bash
# Add multiple targets
python main.py add-target 0xAAAA...1111 Trader1 --notes "BTC specialist"
python main.py add-target 0xBBBB...2222 Trader2 --notes "ETH specialist"
python main.py add-target 0xCCCC...3333 Trader3 --notes "Multi-asset"

# List all
python main.py list-targets

# Disable one temporarily
python main.py disable-target Trader2

# Check active targets only
python main.py list-targets
```

## Troubleshooting

### Target not being tracked

1. Check if enabled:
   ```bash
   python main.py list-targets --all
   ```
2. Enable if disabled:
   ```bash
   python main.py enable-target NICKNAME
   ```
3. Restart bot:
   ```bash
   sudo systemctl restart polymarket-bot
   ```

### Invalid address error

- Ensure address starts with `0x`
- Ensure address is exactly 42 characters (0x + 40 hex)
- Use lowercase or the command will convert it

### Target already exists

- Use `list-targets` to check existing targets
- Use `update-target` to modify instead of adding

## File Location

- **Local**: `config/targets.json`
- **VPS**: `/home/jmm_deployer/jmm_trade/config/targets.json`

## Backup

```bash
# Backup targets
cp config/targets.json config/targets.backup.json

# Restore from backup
cp config/targets.backup.json config/targets.json
```
