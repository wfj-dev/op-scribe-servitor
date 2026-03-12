# OP-Scribe Servitor Architecture

```mermaid
flowchart TB
    subgraph Discord["Discord Platform"]
        Users["Discord Users"]
        AARChannel["AAR Channels"]
        DataVault["❖⋅data-vault⋅❖"]
    end

    subgraph Bot["bot.py - Core Engine"]
        Client["Discord.py Client"]
        Commands["Slash Commands (21)"]
        Events["Event Handlers"]
        
        subgraph Tasks["Scheduled Tasks"]
            DailyAudit["Daily Audit"]
            MonthlyAudit["Monthly Full Audit"]
            WeeklyMaint["Weekly Maintenance"]
        end
        
        subgraph Locks["Concurrency Control"]
            ReconcileLock["RECONCILE_LOCK"]
            RitesLock["RITES_LOCK"]
            RotationLock["ROTATION_LOCK"]
            ActivityLock["ACTIVITY_STATUS_LOCK"]
        end
        
        Parser["AAR Parser"]
    end

    subgraph DataStore["datastore.py - Data Layer"]
        Cache["In-Memory Cache"]
        UserStats["User Stats Cache"]
        HomeChapter["Home Chapter Cache (7d TTL)"]
        FlushTask["Background Flush (60s)"]
    end

    subgraph Config["config/config.json"]
        GuildConfig["Guild Settings"]
        Permissions["Role-Based Permissions"]
        ChannelPolicies["Channel Policies"]
        Milestones["Milestone Thresholds"]
    end

    subgraph Data["data/*.json"]
        AARRecords["aar_records.json"]
        ProcessedIDs["processed_ids.json"]
        AARErrors["aar_errors.json"]
        ActivityStatus["activity_status.json"]
        MilestoneTracking["milestone_tracking.json"]
        PromotionTracking["promotion_tracking.json"]
        Rites["rites.json"]
        TrophyHall["trophy_hall_index.json"]
        HomeRotation["home_chapter_rotation.json"]
        Oaths["oaths_index.json"]
    end

    subgraph Logging["logs/"]
        LogFile["op-scribe-servitor.log"]
    end

    Users -->|"Slash Commands"| Commands
    AARChannel -->|"AAR Messages"| Parser
    Parser --> Cache
    Commands --> Cache
    Commands --> DataVault
    Events --> Cache
    Tasks --> Parser
    Tasks --> Cache
    
    Cache --> FlushTask
    FlushTask -->|"Atomic Write + .bak"| Data
    
    Client --> Config
    Commands --> Permissions
    
    Bot --> Logging
```

## Component Overview

| Component | File | Responsibility |
|-----------|------|----------------|
| Core Engine | `bot.py` | Discord client, slash commands, event handlers, scheduled tasks |
| Data Layer | `datastore.py` | Write-behind cache, background flush, user stats |
| Configuration | `config/config.json` | Guild settings, permissions, channel policies |
| Persistence | `data/*.json` | AAR records, activity tracking, milestones, rites |

## Data Flow

1. **AAR Messages** → Parsed from Discord channels
2. **In-Memory Cache** → Records stored in datastore
3. **Background Flush** → Atomic writes every 60 seconds with `.bak` backups
4. **Slash Commands** → Query cache for real-time reporting
