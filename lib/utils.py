"""
Part of the SqueezeMeta distribution. 25/03/2018 Original version, (c) Fernando Puente-Sánchez, CNB-CSIC.
python utilities for working with SqueezeMeta results
"""

from collections import defaultdict
from numpy import array, isnan, seterr
seterr(divide='ignore', invalid='ignore')

TAXRANKS = ('superkingdom', 'phylum', 'class', 'order', 'family', 'genus', 'species')
TAXRANKS_SHORT = ('k', 'p', 'c', 'o', 'f', 'g', 's')

def parse_conf_file(project_path, override = {}):
    """
    Parse the configuration file containing all the information relevant fot a given SqueezeMeta project.
    Return a dictionary in which the key is the corresponding SqueezeMeta_conf.pl variable (with the leading sigil, as in "$aafile") and the value is the value of the said variable.
    Variable values can be overriden by providing a dictionary of structure {var_to_override: new_value}.
    """
    perlVars = {}
    for line in open('{}/SqueezeMeta_conf.pl'.format(project_path)):
        line = line.rsplit('#',1)[0] # Remove comment strings.
        if line.startswith('$'): # Is this a var definition?
            var, value = [x.strip(' \'\"') for x in line.strip().strip(';').split('=',1)]
            value = value if var not in override else override[var]
            perlVars[var] = value

    ### Define this bc it's funny to parse perl code with python.
    def perl_string_interpolation(string):
        if '$' in string:
            for var in perlVars:
                if var in string and not '\\{}'.format(var) in string: # The var is in the string, and the $ is not escaped like "\$"
                    string = string.replace(var, perl_string_interpolation(perlVars[var])) # Recursive interpolation.
        return string


    ### Back to work. Interpolate all the strings.
    for var, value in perlVars.items():
        perlVars[var] = perl_string_interpolation(value)

    return perlVars



def parse_orf_table(orf_table, nokegg, nocog, nopfam, trusted_only, ignore_unclassified_fun, custom_methods=None, orfSet=None):
    """
    Parse a orftable generated by SqueezeMeta.
    Return:
        samplenames: list of sample names in the project
        orfs: a dictionary with the following keys
            abundances: a dictionary with orfs as keys, and a numpy array of abundances per sample
                        (samples sorted as in samplenames)
            copies: a dictionary with orfs as keys, and a numpy array of copies per sample
                    (in this case either 0 or 1 depending on whether the ORF is present or not
                     in each sample)
            length: a dictionary with orfs as keys, and a numpy array of total length per sample
                    (in this case either 0 or the length of the ORF,  depending on whether the ORF
                     is present or not in each sample)
            tpm: a dictionary with orfs as keys and a numpy array of tpms per sample.
        kegg, cog, pfam: dictionaries with the same structure as the orfs dictionary.
            the copies and length subdictionaries will contain the number of orfs from each kegg|cog|pfam
            in the different samples, or the aggregated length of each kegg|cog|pfam in the different
            samples, respectively. In the case of kegg and cog, the info subdictionary will
            contain function names and hierarchies.
        custom: for each custom annotation method, a dictionary with the same structure as kegg/cog.
    """

    orfs = {res: {}               for res in ('abundances', 'bases', 'coverages', 'copies', 'lengths')} # I know I'm being inconsistent with camelcase and underscores... ¯\_(ツ)_/¯
    kegg = {res: defaultdict(int) for res in ('abundances', 'bases', 'coverages', 'copies', 'lengths')}
    kegg['info'] = {}
    cog  = {res: defaultdict(int) for res in ('abundances', 'bases', 'coverages', 'copies', 'lengths')}
    cog['info'] = {}
    pfam = {res: defaultdict(int) for res in ('abundances', 'bases', 'coverages', 'copies', 'lengths')}
    custom = {method: {res: defaultdict(int) for res in ('abundances', 'bases', 'coverages', 'copies', 'lengths')} for method in custom_methods}
    [custom[method].update({'info': {}}) for method in custom]


    ### Define helper functions.
    def update_dicts(funDict, funIdx, trusted_only):
        # abundances, coverages, copies, lengths and ignore_unclassified_fun are taken from the outer scope.
        if trusted_only and line[funIdx] and line[funIdx][-1] != '*': # Functions confirmed with the bestaver algorithm have a trailing asterisk.
            pass
        else:
            funs = line[funIdx].replace('*','')
            funs = ['Unclassified'] if not funs else funs.strip(';').split(';') # So much fun!
            for fun in funs:
                if ignore_unclassified_fun and fun == 'Unclassified':
                    continue
                # If we have a multi-KO annotation, split counts between all KOs.
                funDict['abundances'][fun] += abundances / len(funs)
                funDict['bases'][fun]      += bases / len(funs)
                funDict['coverages'][fun]  += coverages / len(funs)
                funDict['copies'][fun]     += copies # We treat every KO as an individual smaller gene: less size, less reads, one copy.
                funDict['lengths'][fun]    += lengths / len(funs)


    def tpm(funDict):
        # Calculate reads per kilobase.    
        fun_avgLengths = {fun: funDict['lengths'][fun] / funDict['copies'][fun] for fun in funDict['lengths']} # NaN appears if a fun has no copies in a sample.
        fun_rpk = {fun: funDict['bases'][fun] / (fun_avgLengths[fun]/1000) for fun in funDict['bases']}

        # Remove NaNs.
        for fun, rpk in fun_rpk.items():
            rpk[isnan(rpk)] = 0

        # Get tpm.    
        fun_tpm = normalize_abunds(fun_rpk, 1000000)
        return fun_tpm


    ### Do stuff.
    with open(orf_table) as infile:

        infile.readline() # Burn comment.
        header = infile.readline().strip().split('\t')
        idx =  {h: i for i,h in enumerate(header)}
        samples = [h for h in header if 'Raw read count' in h]
        samplesBases = [h for h in header if 'Raw base count' in h]
        samplesCov = [h for h in header if 'Coverage' in h]
        sampleNames = [s.replace('Raw read count ', '') for s in samples]

        for line in infile:
            line = line.strip().split('\t')
            orf = line[idx['ORF ID']]
            if orfSet and orf not in orfSet:
                continue
            length = line[idx['Length NT']]
            length = int(length) if length else 0 # Fix for rRNAs being in the ORFtable but having no length.
            if not length:
                print(line)
                continue # print and continue for debug info.

            abundances = array([int(line[idx[sample]]) for sample in samples])
            bases = array([int(line[idx[sample]]) for sample in samplesBases])
            coverages = array([float(line[idx[sample]]) for sample in samplesCov])
            copies = (abundances>0).astype(int) # 1 copy if abund>0 in that sample, else 0.
            lengths = length * copies   # positive if abund>0 in that sample, else 0.
            orfs['abundances'][orf] = abundances
            orfs['bases'][orf] = bases
            orfs['coverages'][orf] = coverages
            orfs['copies'][orf] = copies
            orfs['lengths'][orf] = lengths

            if not nokegg:
                ID = line[idx['KEGG ID']].replace('*', '')
                if ID:
                    kegg['info'][ID] = [line[idx['KEGGFUN']], line[idx['KEGGPATH']]]
                update_dicts(kegg, idx['KEGG ID'], trusted_only)
            if not nocog:
                ID = line[idx['COG ID']].replace('*', '')
                if ID:
                    cog['info'][ID] = [line[idx['COGFUN']], line[idx['COGPATH']]]
                update_dicts(cog, idx['COG ID'], trusted_only)
            if not nopfam:
                update_dicts(pfam, idx['PFAM'], False) # PFAM are not subjected to the bestaver algorithm.
            for method in custom_methods:
                ID = line[idx[method]].replace('*', '')
                if ID:
                    custom[method]['info'][ID] = [ line[idx[method + ' NAME']] ]
                update_dicts(custom[method], idx[method], trusted_only)

    # Calculate tpm.
    orfs['tpm'] = tpm(orfs)
    if not nokegg:
        kegg['tpm'] = tpm(kegg)
    if not nocog:
        cog['tpm']  = tpm(cog)
    if not nopfam:
        pfam['tpm'] = tpm(pfam)
    for method in custom_methods:
        custom[method]['tpm'] = tpm(custom[method])

    # If RecA/RadA is present (it should!), calculate copy numbers.
    if not nocog and 'COG0468' in cog['coverages']:
        RecA = cog['coverages']['COG0468']
        kegg['copyNumber'] = {k: cov/RecA for k, cov in kegg['coverages'].items()}
        cog['copyNumber']  = {k: cov/RecA for k, cov in cog['coverages'].items() }
        pfam['copyNumber'] = {k: cov/RecA for k, cov in pfam['coverages'].items()}
        for method in custom_methods:
            custom[method]['copyNumber'] = {k: cov/RecA for k, cov in custom[method]['coverages'].items()}
    else:
        print('COG0468 (RecA/RadA) was not present in your data. This is weird, as RecA should be universal, so you probably just skipped COG annotation. Skipping copy number calculation...')

    return sampleNames, orfs, kegg, cog, pfam, custom


def read_orf_names(orf_table):
    """
    Parse a orftable generated by SqueezeMeta.
    Return a set with the names of all the orfs.
    """
    with open(orf_table) as infile:
        infile.readline() # Burn comment.
        infile.readline() # Burn headers.
        return {line.split('\t')[0] for line in infile}


def parse_tax_table(tax_table):
    """
    Parse a fun3 taxonomy file from SqueezeMeta.
    Return:
        orf_tax: dictionary with orfs as keys and taxonomy list as values
        orf_tax_wranks: same, but with prefixes indicating each taxonomic rank (eg "p_<phylum>")
    """
    orf_tax = {}
    orf_tax_wranks = {}
    with open(tax_table) as infile:
        infile.readline() # Burn comment.
        for line in infile:
            line = line.strip().split('\t')
            if len(line) == 1:
                orf, tax = line[0], 'n_Unclassified' # Add mock empty taxonomy, as the read is fully unclassified. 
            else:
                orf, tax = line
            orf_tax[orf], orf_tax_wranks[orf] = parse_tax_string(tax)
    return orf_tax, orf_tax_wranks


def parse_contig_table(contig_table):
    """
    Parse a contig table generated by SqueezeMeta
    Return
        contig_abunds: dictionary with contigs as keys and a numpy array of contig abundances across 
                       samples as values.
        contig_tax: dictionary with contigs as keys and taxonomy lists as values
        contig_tax_wranks: same, but with prefixes indicating each taxonomic rank (eg "p_<phylum>")
    """
    contig_tax = {}
    contig_tax_wranks = {}
    contig_abunds = {}
    with open(contig_table) as infile:
        infile.readline() # Burn comment.
        header = infile.readline().strip().split('\t')
        idx =  {h: i for i,h in enumerate(header)}
        samples = [h for h in header if 'Raw read count' in h]
        sampleNames = [s.replace('Raw read count ', '') for s in samples]
        for line in infile:
            line = line.strip().split('\t')
            contig, tax = line[idx['Contig ID']], line[idx['Tax']]
            if not tax:
                tax = 'n_Unclassified' # Add mock empty taxonomy, as the read is fully unclassified.
            contig_tax[contig], contig_tax_wranks[contig] = parse_tax_string(tax)
            contig_abunds[contig] = array([int(line[idx[sample]]) for sample in samples])

    return contig_abunds, contig_tax, contig_tax_wranks


def parse_bin_table(bin_table):
    """
    Parse a contig table generated by SqueezeMeta
    Return
        bin_tpms: dictionary with contigs as keys and a numpy array of bin tpms across 
                       samples as values.
        bin_tax: dictionary with contigs as keys and taxonomy lists as values
        bin_tax_wranks: same, but with prefixes indicating each taxonomic rank (eg "p_<phylum>")
    """
    bin_tpm = {}
    bin_tax = {}
    bin_tax_wranks = {}
    with open(bin_table) as infile:
        infile.readline() # Brun comment.
        header =  infile.readline().strip().split('\t')
        idx =  {h: i for i,h in enumerate(header)}
        samples = [h for h in header if 'TPM' in h]
        sampleNames = [s.replace('TPM ', '') for s in samples]
        for line in infile:
            line = line.strip().split('\t')
            bin_, tax = line[idx['Bin ID']], line[idx['Tax']]
            if not tax or tax=='No consensus':
                tax = 'n_Unclassified' # Add mock empty taxonomy, as the read is fully unclassified.
            bin_tax[bin_], bin_tax_wranks[bin_] = parse_tax_string(tax)
            bin_tpm[bin_] = array([float(line[idx[sample]]) for sample in samples])
    return bin_tpm, bin_tax, bin_tax_wranks


def parse_tax_string(taxString):
    """
    Parse a taxonomy string as reported by the fun3 algorithm in SqueezeMeta
    Return:
        taxList: list of taxa sorted by rank
            [<superkingdom>, <phylum>, <class>, <order>, <family>, <genus>, <species>]
        taxList_wranks: same as taxlist, but with a prefix indicating each rank (eg "p_<phylum>")
    """
    taxDict = dict([r.split('_', 1) for r in taxString.strip(';').split(';')]) # We only preserve the last "no_rank" taxonomy, but we don't care.
    taxList = []
    lastRankFound = ''
    for rank in reversed(TAXRANKS_SHORT): # From species to superkingdom.
        if rank in taxDict:
            lastRankFound = rank
            taxList.append(taxDict[rank])
        elif lastRankFound:
            # This rank is not present,  but we have a classification at a lower rank.
            # This happens in the NCBI tax e.g. for some eukaryotes, they are classified at the class but not at the phylum level.
            # We inherit lower rank taxonomies, as we actually have classified that ORF.
            taxList.append('{} (no {} in NCBI)'.format(taxDict[lastRankFound], TAXRANKS[TAXRANKS_SHORT.index(rank)]))
        else:
            # Neither this or lower ranks were present. The ORF is not classified at this level.
            pass
    # Now add strings for the unclassified ranks.
    unclassString = 'Unclassified {}'.format(taxList[0]) if taxList else 'Unclassified'
    while len(taxList) < 7:
        taxList.insert(0, unclassString)

    # Reverse to retrieve the original order.
    taxList.reverse()

    # Generate comprehensive taxonomy strings.
    taxList_wranks = []
    for i, rank in enumerate(TAXRANKS_SHORT):
        newStr = '{}_{}'.format(rank, taxList[i])
        if i>0:
            newStr = '{};{}'.format(taxList_wranks[-1], newStr)
        taxList_wranks.append(newStr)

    return taxList, taxList_wranks


def aggregate_tax_abunds(orf_abunds, orf_tax, rankIdx):
    """
    Aggregate the abundances of all orfs belonging to the same taxon at a given taxonomic rank.
    Return:
        tax_abunds: dictionary with taxa as keys, numpy array aggregated abundances as values
    """
    tax_abunds = defaultdict(int)
    for orf, abunds in orf_abunds.items():
        if orf not in orf_tax:
            #print('{} had no hits against nr!'.format(orf))
            continue
        tax = orf_tax[orf][rankIdx]
        tax_abunds[tax] += abunds
    return tax_abunds


def normalize_abunds(abundDict, scale=1):
    """
    Normalize a dictionary of item abundances to a common scale.
    """
    abundPerSample = 0
    for row, abund in abundDict.items():
        abundPerSample += abund
    return {row: (scale * abund / abundPerSample) for row, abund in abundDict.items()}


def parse_fasta(fasta):
    """
    Parse a fasta file into a dictionary {header: sequence}
    """
    res = {}
    with open(fasta) as infile:
        header = ''
        seq = ''
        for line in infile:
            if line.startswith('>'):
                if header:
                    res[header] = seq
                    seq = ''
                header = line.strip().lstrip('>').split(' ')[0].split('\t')[0]
            else:
                seq += line.strip()
        res[header] = seq
    return res


def write_orf_seqs(orfs, aafile, fna_blastx, rrnafile, trnafile, outname):
    ### Create sequences file.
    # Load prodigal results.
    ORFseq = parse_fasta(aafile)
    # Load blastx results if required.
    if fna_blastx:
        ORFseq.update(parse_fasta(fna_blastx))
    if rrnafile:
        ORFseq.update(parse_fasta(rrnafile))
    if trnafile:
        ORFseq.update(parse_fasta(trnafile))
    # Write results.
    with open(outname, 'w') as outfile:
        outfile.write('ORF\tAASEQ\n')
        for ORF in orfs:
            outfile.write('{}\t{}\n'.format(ORF, ORFseq[ORF]))


def write_contig_seqs(contigfile, outname):
    contigseq = parse_fasta(contigfile)
    with open(outname, 'w') as outfile:
        outfile.write('CONTIG\tNTSEQ\n')
        for contig, seq in contigseq.items():
            outfile.write('{}\t{}\n'.format(contig, seq))


def write_row_dict(sampleNames, rowDict, outname):
    """
    rowDict is a dict where keys are features (rows) and values are arrays with the value for each column.
    """
    with open(outname, 'w') as outfile:
        outfile.write('\t{}\n'.format('\t'.join(sampleNames)))
        for row in sorted(rowDict):
            outfile.write('{}\t{}\n'.format(row, '\t'.join(map(str, rowDict[row]))))

