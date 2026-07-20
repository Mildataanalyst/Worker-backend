export const PM_PROFILES = [
  { name:'Milan', tagline:'Vending Machine Devotee', role:'The Gurgaon loyalist', about:'Loves Gurgaon. Loves the office vending machine even more.', img:'/pm/milan.jpg' },
  { name:'Piyush', tagline:'NGO x NGO', role:'Karma points industrialist', about:'Runs an NGO. Works in an NGO. Basically farming karma points at an industrial scale. His desk has battalions, helicopters, mirrors, and approximately 4 square inches of usable space. Known for conspicuously high-neck jackets and a legendary bike that has broken down almost as many times as it has brought him to office.', img:'/pm/piyush.jpg' },
  { name:'Avika', tagline:'Effingut Loyalist', role:'Orange Lay’s economist', about:'Has personally purchased enough orange Lay’s to move national demand curves. Effingut Gurgaon loyalist. Oversees a large food support program, but remains deeply committed to not sharing her own food.', img:'/pm/avika.jpg' },
  { name:'Rachit', tagline:'SoGa Representative', role:'Networker-in-chief', about:'Networker-in-chief. Whoever you know, Rachit probably knows them too — and will somehow say hello to them tomorrow. Most days include a request for banana chips. Most days, that request is not fulfilled.', img:'/pm/rachit.jpg' },
  { name:'Ipshita', tagline:'Green Tea Loyalist', role:'DFP nutrition expert', about:'DFP’s nutrition expert. Loves food so much that when she is not thinking about food, she is watching food reels. Drives every day from Faridabad to office, probably daydreaming about the next meal on the way. Veteran of the team, green tea loyalist, and experienced enough that she may have personally fed the program’s first beneficiary.', img:'/pm/ipshita.jpg' },
  { name:'Kamran', tagline:'Drama Allergy Certified', role:'Unofficial Ministry of Travel', about:'Super experienced, brutally practical, and the unofficial Ministry of Travel. Very allergic to unnecessary drama. Calm operator, straight talker, and the person you want around when a plan needs to survive contact with reality.', img:'/pm/kamran.jpg' },
] as const;

export const DEFAULT_DASHBOARD_DATA = {
  lastUpdated: '',
  states: {
    Karnataka: {
      sheetLink: '',
      totalScanned: 128,
      shortlisted: 24,
      finalShortlist: 24,
      rejected: 104,
      funnelScanned: 37930,
      funnelReview: 496,
      funnelFinal: 24,
      sector: {
        registeredScanned: 37930,
        noOfficialWebsite: 29112,
        noOfficialWebsitePct: 76.8,
        wrongWebsite: 5397,
        wrongWebsitePct: 14.2,
        unreachable: 1285,
        unreachablePct: 3.4,
        enoughPublicInfo: 2136,
        enoughPublicInfoPct: 5.6,
        metroSkewText: 'Bengaluru Urban accounts for 39.7% of the visible shortlist, suggesting that digitally legible NGOs cluster more in urban districts.'
      },
      ngos: [
        { name:'Akshaya Seva Foundation', location:'Bengaluru Rural', type:'Nutrition', summary:'Supports community meal access and local nutrition programs.', url:'' },
        { name:'Swasthya Mitra Trust', location:'Mysuru', type:'Health', summary:'Works with local health workers on preventive care programs.', url:'' },
        { name:'Annapurna Rural Network', location:'Raichur', type:'Food security', summary:'Runs community-led food access and household support initiatives.', url:'' },
        { name:'Kaliyuva Mane', location:'Mysuru', type:'Residential learning', summary:'Runs a child-focused residential learning centre for children from underserved families.', url:'' },
        { name:'Shishu Mandir', location:'Bengaluru Urban', type:'Education', summary:'Provides schooling and support services for children from low-income communities.', url:'' },
        { name:'Mahesh Foundation', location:'Belagavi', type:'Child care', summary:'Works with vulnerable children through residential care, education and nutrition support.', url:'' },
        { name:'Kalkeri Sangeet Vidyalaya', location:'Dharwad', type:'Arts education', summary:'Combines academics, music and residential support for children from rural communities.', url:'' },
        { name:'Bridges of Sports', location:'Uttara Kannada', type:'Sports pathway', summary:'Uses athletics and structured coaching to create opportunities for children and youth.', url:'' }
      ]
    }
  },
  pmStats: {}
} as const;
