import { useState } from 'react';

interface FAQItem {
  question: string;
  answer: string;
}

interface FAQSection {
  title: string;
  icon: string;
  items: FAQItem[];
}

const SECTIONS: FAQSection[] = [
  {
    title: 'Getting Started',
    icon: '1',
    items: [
      {
        question: 'What is Sports Picks?',
        answer:
          'Sports Picks is a data-driven sports betting analysis tool. It pulls live game schedules, odds, and player prop lines from ESPN and The Odds API, then runs them through configurable strategies to generate picks with confidence ratings. It covers NBA, NFL, NCAAB, NCAAF, Boxing, and MMA.',
      },
      {
        question: 'How do I load data into the app?',
        answer:
          'Go to the Backtesting page and click the "Run Pipeline" button. This fetches today\'s games from ESPN, pulls current odds and player props from The Odds API, and generates picks using your active strategy. The pipeline runs automatically on a schedule, but you can trigger it manually at any time.',
      },
      {
        question: 'What sports are supported?',
        answer:
          'NBA, NFL, NCAAB (men\'s college basketball), NCAAF (college football), Boxing, and MMA. Each sport is only active during its season. NBA and NCAAB are active from October through June, NFL from September through February, and Boxing/MMA are year-round. You can filter by sport on every page using the sport tabs.',
      },
    ],
  },
  {
    title: "Today's Picks",
    icon: '2',
    items: [
      {
        question: "What does the Today's Picks page show?",
        answer:
          "This is the main dashboard. It shows algorithm-generated picks for today's games, a summary of your overall record, today's game schedule with live odds, and a Top Props section highlighting the highest-edge player prop bets.",
      },
      {
        question: 'What are the summary cards at the top?',
        answer:
          'The summary cards show your cumulative betting record: Win Rate (percentage of winning picks), Total ROI (return on investment assuming flat $100 bets), and your overall W-L record. These update as pick results are graded.',
      },
      {
        question: 'What do the columns in the Games & Odds table mean?',
        answer:
          'Sport \u2014 which league the game belongs to. Matchup \u2014 away team @ home team. Status \u2014 scheduled, in progress, or final. Spread \u2014 the point spread for the home team (negative means favored). ML Home / ML Away \u2014 moneyline odds for each team (negative = favorite, positive = underdog; e.g., -200 means bet $200 to win $100). O/U \u2014 the over/under total points line. Book \u2014 which sportsbook the odds come from.',
      },
      {
        question: 'What does a moneyline like -200 or +150 mean?',
        answer:
          'American odds: a negative number (e.g., -200) means you must bet that amount to win $100 \u2014 the team is the favorite. A positive number (e.g., +150) means a $100 bet wins that amount \u2014 the team is the underdog. The larger the negative number, the heavier the favorite.',
      },
      {
        question: 'What is a spread?',
        answer:
          'The spread is the projected margin of victory. If a team has a spread of -5.5, they must win by 6 or more points for a spread bet to pay out. A spread of +5.5 means the team can lose by up to 5 points and a bet on them still wins. It levels the playing field between mismatched teams.',
      },
      {
        question: 'What is the over/under (O/U)?',
        answer:
          'The over/under is the projected total combined score of both teams. You can bet "Over" (more total points) or "Under" (fewer total points). For example, an O/U of 220.5 for an NBA game means oddsmakers expect about 220 total points between both teams.',
      },
    ],
  },
  {
    title: 'Player Props',
    icon: '3',
    items: [
      {
        question: 'What are player props?',
        answer:
          'Player props are bets on individual player performance rather than game outcomes. Examples: "LeBron James Over 25.5 Points" or "Patrick Mahomes Under 280.5 Pass Yards." Each prop has a line (the benchmark number), Over/Under odds, and may include an analytical projection.',
      },
      {
        question: 'What do the columns on the Player Props page mean?',
        answer:
          'Player \u2014 the athlete\'s name. Market \u2014 the stat category (Points, Rebounds, Assists, Pass Yards, etc.). Line \u2014 the benchmark set by the sportsbook. Over/Under \u2014 the odds for each side of the line. Book \u2014 which sportsbook posted the line. Proj \u2014 our statistical projection for the player\'s actual output. Edge \u2014 the percentage difference between our projection and the line. Conf \u2014 confidence rating (1\u20135 stars). Source \u2014 where the projection data came from (e.g., season average, recent games).',
      },
      {
        question: 'What does Edge % mean?',
        answer:
          'Edge is calculated using statistical distributions rather than simple averages. The system computes the probability that a player will exceed (or fall short of) the line using Normal or Poisson distributions based on their game-by-game variance. Edge % = (exceedance probability - 50%) \u00d7 2. For example, if there\'s a 75% statistical probability a player exceeds the line, the edge is 50%. This is more accurate than simple average-vs-line comparisons because it accounts for player consistency.',
      },
      {
        question: 'What do the confidence stars mean?',
        answer:
          'Confidence is rated 1\u20135 stars based on the quality and quantity of data behind a projection. 5 stars means strong recent data closely matches the season average and both clearly favor one side. 1 star means limited data or conflicting signals. You can filter by minimum confidence using the dropdown.',
      },
      {
        question: 'What does "stale" mean next to a source?',
        answer:
          'A "stale" badge means the player stat data used for the projection is outdated (typically more than 7 days old). The projection may be less reliable. Fresh data is pulled when you run the pipeline.',
      },
      {
        question: 'What prop markets are available?',
        answer:
          'NBA: Points, Rebounds, Assists, 3-Pointers, Blocks, Steals, Turnovers, and combo markets (Pts+Reb+Ast, Pts+Reb, etc.). NFL: Pass Yards, Rush Yards, Receiving Yards, Pass TDs, Anytime TD, Receptions. The available markets depend on what the Odds API provides for each sport.',
      },
      {
        question: 'How does opponent adjustment work for props?',
        answer:
          'Every prop projection is adjusted based on the opposing team\'s defensive rating. The system calculates how much better or worse the opponent\'s defense is compared to the league average (110 baseline). A player facing an elite defense (rating ~100) has their projection reduced by ~9%, while a player facing a poor defense (rating ~120) gets a ~9% boost. This prevents betting Over on a scorer facing the #1 defense in the league.',
      },
      {
        question: 'What is game script correlation?',
        answer:
          'Game script links game-level predictions to prop analysis. If the model predicts a blowout (margin > 7 points), it adjusts player props accordingly. For the winning team: passing yards decrease (clock management), rushing yards increase, and NBA stars\' stats decrease (they sit in the 4th quarter). For the losing team: passing yards increase (throwing to catch up) and rushing yards decrease. The adjustment scales with the predicted margin, up to 15%.',
      },
    ],
  },
  {
    title: 'Backtesting',
    icon: '4',
    items: [
      {
        question: 'What is backtesting?',
        answer:
          'Backtesting runs a strategy against historical data to see how it would have performed. You define a strategy with specific parameters, pick a date range, and the system simulates the picks it would have made and checks them against actual results. This helps you evaluate and tune strategies before using them for real picks.',
      },
      {
        question: 'What are the strategy types?',
        answer:
          'Game strategies predict game outcomes (moneyline, spread, over/under). Prop strategies predict player performance (over/under on individual stats). The default "ensemble" strategy combines statistical analysis with ELO ratings for game predictions. The "prop_value" strategy identifies edges in player prop markets.',
      },
      {
        question: 'What does "Promote" do?',
        answer:
          'Promoting a strategy makes it the active strategy used for generating daily picks. Only one strategy of each type (game/prop) can be active at a time. When you promote a new strategy, the previous active one is deactivated.',
      },
      {
        question: 'What is the Performance Over Time chart?',
        answer:
          'This chart shows your daily win/loss record and cumulative profit over time. Green bars represent winning days, red bars represent losing days. It helps you visualize trends and streaks in your pick performance.',
      },
      {
        question: 'What do the backtest result metrics mean?',
        answer:
          'Record \u2014 total wins and losses during the test period. Hit Rate \u2014 percentage of winning picks (55%+ is generally considered strong). ROI \u2014 return on investment assuming flat betting (positive means profitable). By Market \u2014 breaks down results by prop type so you can see which markets the strategy performs best in.',
      },
      {
        question: 'What are strategy config parameters?',
        answer:
          'Each strategy has a JSON config that controls its behavior. Common parameters: min_edge \u2014 minimum edge percentage required to make a pick (higher = fewer but stronger picks). k_factor \u2014 how quickly ELO ratings adjust to new results. lookback \u2014 how many recent games to consider for trend analysis. weights \u2014 how much each model component (point differential, ELO, net rating, home court) contributes to the probability estimate. kelly_fraction \u2014 how aggressive to size bets (default 0.25 = quarter Kelly). ewma_alpha \u2014 how much recent form influences the point differential signal. momentum_weight \u2014 weight given to team momentum in the recent form strategy. You can edit these when creating or modifying a strategy.',
      },
      {
        question: 'What is Auto-Tune?',
        answer:
          'Auto-Tune is an automated optimization feature that tests many different parameter combinations for a strategy and finds the one that performs best. It runs a grid search \u2014 systematically trying different values for min_edge, model weights, lookback periods, and other parameters \u2014 then ranks every configuration by ROI. You can review the top results and apply the best config to your active strategy with one click.',
      },
      {
        question: 'How does Auto-Tune work?',
        answer:
          'Auto-Tune pre-loads all historical games in the date range, then runs the backtester once for each parameter combination. For the ensemble strategy, it tests 20 configurations (5 min_edge values \u00d7 4 weight presets). For prop strategies, it tests 192 combinations (4 min_edge \u00d7 4 recent_weight \u00d7 4 lookback \u00d7 3 min_minutes). Each backtest grades all picks and calculates ROI. Configs producing fewer than 5 picks are discarded to avoid trivial solutions. Results are sorted by ROI and the top 10 are displayed.',
      },
      {
        question: 'What does "Apply Best Config" do?',
        answer:
          'After auto-tuning, you can apply the winning configuration to your active strategy. This updates the strategy\'s parameters in the database so that future picks are generated using the optimized settings. The original config is overwritten \u2014 if you want to preserve it, create a new strategy variant first.',
      },
    ],
  },
  {
    title: 'Track Record',
    icon: '5',
    items: [
      {
        question: 'What does the Track Record page show?',
        answer:
          'A comprehensive history of all picks the system has made. It includes your overall Win Rate, ROI, and W-L record at the top, a calendar heatmap showing daily results, and a detailed table of every past pick with its result.',
      },
      {
        question: 'How is ROI calculated?',
        answer:
          'ROI assumes flat $100 bets on every pick. Winning bets pay out based on the odds at the time of the pick. ROI = (total profit / total amount wagered) \u00d7 100. A +10% ROI means for every $100 wagered, you profited $10 on average.',
      },
      {
        question: 'What are the pick result statuses?',
        answer:
          'Won (green) \u2014 the pick was correct. Lost (red) \u2014 the pick was incorrect. Push (yellow) \u2014 the result landed exactly on the line, and the bet is returned. Pending \u2014 the game hasn\'t finished yet and the pick hasn\'t been graded.',
      },
      {
        question: 'How does the calendar heatmap work?',
        answer:
          'Each cell represents a day. The color intensity shows performance: green for profitable days, red for losing days, darker shades for larger gains or losses. Hovering over a cell shows the exact record and profit for that day.',
      },
    ],
  },
  {
    title: 'Data Pipeline',
    icon: '6',
    items: [
      {
        question: 'Where does the data come from?',
        answer:
          'Game schedules and scores come from ESPN\'s public API. Odds (moneylines, spreads, over/unders) and player props come from The Odds API, which aggregates lines from major sportsbooks like DraftKings, FanDuel, BetMGM, and others. Player stats for projections come from basketball-reference and ESPN stats.',
      },
      {
        question: 'How often is data refreshed?',
        answer:
          'The pipeline can be run manually from the Backtesting page at any time. When run, it fetches the latest games, odds, and props in real-time. Odds change frequently throughout the day, so running the pipeline closer to game time gives the most current lines.',
      },
      {
        question: 'Why do some games show no odds?',
        answer:
          'Some games may not have odds available yet if they\'re too far in the future, or if The Odds API doesn\'t cover that particular matchup. Boxing and MMA odds may appear before game schedules since those sports don\'t have ESPN scoreboard coverage \u2014 games are created directly from the odds data.',
      },
      {
        question: 'What does the pipeline actually do step by step?',
        answer:
          '1) Fetches today\'s game schedules from ESPN for each active sport. 2) Fetches current odds from The Odds API and links them to games. 3) Fetches player prop lines from The Odds API. 4) Generates picks using the active strategy, including schedule analysis, opponent adjustments, and game script correlation. For boxing and MMA, step 2 also creates the game entries since those sports don\'t have ESPN coverage.',
      },
    ],
  },
  {
    title: 'Prediction Engine',
    icon: '7',
    items: [
      {
        question: 'How does the calibrated prediction model work?',
        answer:
          'The ensemble strategy uses a logistic regression model trained on historical game outcomes. It takes five features \u2014 ELO difference, point differential difference, net rating difference, rest days difference, and pace difference \u2014 and outputs a calibrated probability of the home team winning. This replaces the older hardcoded sigmoid functions. The model trains on at least 30 completed games and falls back to the heuristic approach when insufficient data exists.',
      },
      {
        question: 'What is distribution-based prop analysis?',
        answer:
          'Instead of simply comparing a player\'s average to the sportsbook line, the system calculates the statistical probability of exceeding the line using the player\'s game-by-game variance. For continuous stats like Points and Yards, it uses a Normal (bell curve) distribution. For count-based stats like Rebounds, Assists, and 3-Pointers (when the average is below 10), it uses a Poisson distribution. This means a consistent player averaging 20 points is treated differently from a volatile player averaging 20 points.',
      },
      {
        question: 'What is EWMA momentum?',
        answer:
          'EWMA (Exponentially Weighted Moving Average) momentum captures whether a team is trending up or down relative to their season baseline. The system compares recent win percentage to overall season win percentage for both teams. A team on a hot streak gets a probability boost, while a slumping team gets penalized. The point differential signal is also blended with recent form using a configurable ewma_alpha parameter (default 0.3).',
      },
      {
        question: 'What is Kelly Criterion bet sizing?',
        answer:
          'Every pick includes a suggested unit size based on the Kelly Criterion formula: f* = (bp - q) / b, where b is the decimal odds minus 1, p is the model\'s win probability, and q is 1 - p. The system uses fractional Kelly (default 25%) to reduce variance. One unit is 1% of your bankroll, so 1.0 units on a $1,000 bankroll is a $10 bet. The suggested size ranges from 0.5 units (a real but small edge) to 3.0 units, which is the cap and is reached around an 8-point edge at -110. A suggested size of 0 means the model declines the bet \u2014 at that price the wager is negative expected value, and no stake is the correct answer.',
      },
      {
        question: 'What is schedule fatigue detection?',
        answer:
          'For NBA and NCAAB, the system checks if a team is playing their 3rd game in 4 nights. Teams in this situation are statistically fatigued and underperform. The model applies a probability penalty of up to 3% based on the severity (how many games in the window). This helps avoid backing tired teams, especially on the second night of a back-to-back.',
      },
      {
        question: 'What are lookahead spots?',
        answer:
          'A lookahead spot occurs when a heavily favored team (100+ ELO advantage over their current opponent) has a tough game coming up next. For NFL and NCAAF, this looks 10 days ahead; for other sports, 4 days. Teams in lookahead spots historically underperform the spread because they\'re mentally preparing for next week\'s big game. The model applies a 4% probability penalty to account for this.',
      },
      {
        question: 'What is Closing Line Value (CLV)?',
        answer:
          'CLV measures whether your model is beating the market. When a pick is graded, the system records the closing odds (the final odds just before the game starts) alongside the odds when the pick was made. If you consistently pick teams at -110 that close at -130, your model is identifying value before the market corrects. Positive CLV is the gold standard for long-term profitability in sports betting \u2014 even if individual games lose, consistently beating the closing line means your model is profitable over time.',
      },
    ],
  },
  {
    title: 'Paper Trading',
    icon: '8',
    items: [
      {
        question: 'What is Paper Trading?',
        answer:
          'Paper Trading lets you and your friends compete with simulated bets using a virtual bankroll. Everyone starts with $10,000 in play money. You pick games, set your stake, and the system tracks your results over time. It\u2019s a risk-free way to test your sports betting instincts against your friends without putting up real cash.',
      },
      {
        question: 'How do I join?',
        answer:
          'Go to the Paper Trading page, type your name in the input field, and click "Join" (or press Enter). Each name must be unique. Once created, your profile appears on the leaderboard with a $10,000 starting balance.',
      },
      {
        question: 'How do I place a pick?',
        answer:
          'Click your name on the leaderboard to open your profile. In the "Place a Pick" form, select a game, choose your bet type (Moneyline, Spread, or Over/Under), and set your stake. The odds auto-fill based on the game and bet type, but you can adjust them. Click "Place Pick" to submit. Your balance is reduced by the stake amount until the pick is graded.',
      },
      {
        question: 'How does grading work?',
        answer:
          'If a game is already final when you place your pick, it\u2019s graded instantly. For live or upcoming games, picks stay "Pending" until you (or anyone) clicks the "Grade Picks" button. Grading checks all pending picks against final scores and updates results and balances. Wins pay out based on the odds (American odds format), losses deduct your stake, and pushes return your stake with no profit.',
      },
      {
        question: 'How is the leaderboard ranked?',
        answer:
          'The leaderboard ranks users by current balance (highest first). It also shows each user\u2019s profit, ROI, win-loss record, win percentage, and number of pending picks. Click any user to see their full stats and pick history.',
      },
      {
        question: 'What are the period stats (Today, This Week, This Month)?',
        answer:
          'When you select a user, a breakdown table shows their record, win rate, profit, and ROI across four time periods: Today, This Week (starting Monday), This Month (starting the 1st), and All Time. This lets you spot hot streaks and slumps. All stats persist in the database \u2014 they\u2019re calculated from your full pick history, not just the current session.',
      },
      {
        question: 'What bet types are available?',
        answer:
          'Three types: Moneyline (pick the winner), Spread (pick a team to cover the point spread), and Over/Under (pick whether the total score goes over or under a number). When you select a game, the form auto-fills a default pick and odds based on the current lines from The Odds API.',
      },
      {
        question: 'Can I run out of money?',
        answer:
          'Yes. If your balance hits zero, you can\u2019t place any more picks. Manage your bankroll wisely \u2014 the Kelly Criterion section in the Prediction Engine FAQ explains optimal bet sizing. A common rule of thumb is never risking more than 1-5% of your bankroll on a single pick.',
      },
    ],
  },
];

function Accordion({ item }: { item: FAQItem }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className={`faq-item${open ? ' faq-item-open' : ''}`}
      onClick={() => setOpen(!open)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          setOpen(!open);
        }
      }}
      role="button"
      tabIndex={0}
      aria-expanded={open}
    >
      <div className="faq-question">
        <span>{item.question}</span>
        <span className={`faq-chevron${open ? ' faq-chevron-open' : ''}`}>&#9662;</span>
      </div>
      {open && <div className="faq-answer">{item.answer}</div>}
    </div>
  );
}

export default function FAQ() {
  return (
    <div>
      <div className="page-header">
        <h2 className="page-title">FAQ & User Guide</h2>
      </div>
      <p className="faq-intro">
        Everything you need to know about using Sports Picks &mdash; from reading odds to running backtests.
      </p>

      {SECTIONS.map(section => (
        <div key={section.title} className="faq-section">
          <div className="section-header">
            <span className="faq-section-number">{section.icon}</span>
            {section.title}
            <span className="section-divider" />
          </div>
          <div className="faq-list">
            {section.items.map(item => (
              <Accordion key={item.question} item={item} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
