# mahjong-server-bot-zoo
This repository is the framework for a self hosted mahjong server I (plan to) run. The server can be used to play against your friends and also play against AI opponents for self analysis of your game play. 

# Backend Features
1. Modular Rule sets
    1. Ability to play default MCR, ability to play homerules 
1. Personal account stat tracking
1. Full game history, recording the tile sequence of every game and every players decision for training purposes.
1. In game chat
1. Running Leaderboard and player rank

# Strategic/Analytic Features
1. Togglable weenie mode (all analysis available) vs plain mode (no decoration)
1. Note Taking
1. Tiles currently out list
1. Possible outs list, when calling displays how many Fan a potential hand would have
1. Score calculator built in
1. Automatic game phase tracker, (how many tiles has someone picked up and kept, how many tiles are left in wall)

# Ai Features
1. Shanten analyizer
    1. Quantifies all potential outs that are 2-3 tiles away and how likely they are based on how many tiles are out and also many fan they would be.  
1. Opponent Hand analyzer
    1. analyzes an opponents hand to forecast what outs they might be going for 
# User Interface Features
1. Ability to taunt other players by revealing information about your hand
1. Bilingual in English and Chinese
1. Fun animations 
    1. Plucking????
    1. Dragon Slayer
1. Fun art, such as
    1. Cute pig for websites mascot (The Tile Hog)
    1. unlockable profile pics such as Slobbering Hog when having a high rank, ALL GREEN if you get an ALL green hand. etc etc. 
1. Satisfying sound when placing tiles
1. Achievements:
    1. get a limit hand 
    1. Lose Mahjong to someone else in the turn order when you would have won
    1. Rob a Kong
    1. Win a mahjong game in the first 10 tiles 
    1. Win a mahjong game in the last 10 tiles
    1. Discard 13 orphans

# Roadmap
1. Basic no whistle functionality, can play a game and host on my laptop
2. more whistles
3. host on dedicated 24/7 host
4. AI training and API