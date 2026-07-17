import React, { createContext, useContext, useState, useRef } from 'react';

const AppContext = createContext();

export const AppProvider = ({ children }) => {
  // Manuscript Builder State
  const [manuscriptActive, setManuscriptActive] = useState('abstract');
  const [manuscriptTopic, setManuscriptTopic] = useState('');
  const [manuscriptContent, setManuscriptContent] = useState({});
  const [manuscriptGenerating, setManuscriptGenerating] = useState(false);
  const [manuscriptEditHistory, setManuscriptEditHistory] = useState({});
  const [manuscriptRefs, setManuscriptRefs] = useState(null);
  
  // Ref for tracking the last saved content to prevent unnecessary saves
  const lastSavedContentRef = useRef({});

  // Literature Survey State
  const [litQuery, setLitQuery] = useState('');
  const [litPapers, setLitPapers] = useState([]);
  const [litLoading, setLitLoading] = useState(false);
  const [litActiveTab, setLitActiveTab] = useState('search');
  const [litSearchError, setLitSearchError] = useState('');
  const [litHasSearched, setLitHasSearched] = useState(false);
  const [litLastQuery, setLitLastQuery] = useState('');
  const [litFilterYear, setLitFilterYear] = useState('All');
  const [litFilterSource, setLitFilterSource] = useState('All');
  const [litVisibleCount, setLitVisibleCount] = useState(15);

  const value = {
    // Manuscript
    manuscriptState: {
      active: manuscriptActive, setActive: setManuscriptActive,
      topic: manuscriptTopic, setTopic: setManuscriptTopic,
      content: manuscriptContent, setContent: setManuscriptContent,
      generating: manuscriptGenerating, setGenerating: setManuscriptGenerating,
      editHistory: manuscriptEditHistory, setEditHistory: setManuscriptEditHistory,
      manuscriptRefs, setManuscriptRefs,
      lastSavedContentRef
    },
    // Literature Survey
    literatureState: {
      query: litQuery, setQuery: setLitQuery,
      papers: litPapers, setPapers: setLitPapers,
      loading: litLoading, setLoading: setLitLoading,
      activeTab: litActiveTab, setActiveTab: setLitActiveTab,
      searchError: litSearchError, setSearchError: setLitSearchError,
      hasSearched: litHasSearched, setHasSearched: setLitHasSearched,
      lastQuery: litLastQuery, setLastQuery: setLitLastQuery,
      filterYear: litFilterYear, setFilterYear: setLitFilterYear,
      filterSource: litFilterSource, setFilterSource: setLitFilterSource,
      visibleCount: litVisibleCount, setVisibleCount: setLitVisibleCount,
    }
  };

  return (
    <AppContext.Provider value={value}>
      {children}
    </AppContext.Provider>
  );
};

export const useAppContext = () => useContext(AppContext);
