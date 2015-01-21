import logging
from sv_interval import SVInterval

logger = logging.getLogger(__name__)

'''
From https://github.com/genome/breakdancer, the format is described as 
1. Chromosome 1
2. Position 1
3. Orientation 1
4. Chromosome 2
5. Position 2
6. Orientation 2
7. Type of a SV
8. Size of a SV
9. Confidence Score
10. Total number of supporting read pairs
11. Total number of supporting read pairs from each map file
12. Estimated allele frequency
13. Software version
14. The run parameters

Columns 1-3 and 4-6 are used to specify the coordinates of the two SV breakpoints. The orientation is a string that records the number of reads mapped to the plus (+) or the minus (-) strand in the anchoring regions.

Column 7 is the type of SV detected: DEL (deletions), INS (insertion), INV (inversion), ITX (intra-chromosomal translocation), CTX (inter-chromosomal translocation), and Unknown. 
Column 8 is the size of the SV in bp.  It is meaningless for inter-chromosomal translocations. 
Column 9 is the confidence score associated with the prediction. 
Column 11 can be used to dissect the origin of the supporting read pairs, which is useful in pooled analysis.  For example, one may want to give SVs that are supported by more than one libraries higher confidence than those detected in only one library.  It can also be used to distinguish somatic events from the germline, i.e., those detected in only the tumor libraries versus those detected in both the tumor and the normal libraries.
Column 12 is currently a placeholder for displaying estimated allele frequency. The allele frequencies estimated in this version are not accurate and should not be trusted.
Column 13 and 14 are information useful to reproduce the results.

Example 1:
1 10000 10+0- 2 20000 7+10- CTX -296 99 10 tB|10 1.00 BreakDancerMax-0.0.1 t1

An inter-chromosomal translocation that starts from chr1:10000 and goes into chr2:20000 with 10 supporting read pairs from the library tB and a confidence score of 99.

Example 2:
1 59257 5+1- 1 60164 0+5- DEL 862 99 5 nA|2:tB|1 0.56 BreakDancerMax-0.0.1 c4

A deletion between chr1:59257 and chr1:60164 connected by 5 read pairs, among which 2 in library nA and 1 in library tB support the deletion hypothesis. This deletion is detected by BreakDancerMax-0.0.1 with a separation threshold of 4 s.d.

Example 3:
1 62767 10+0- 1 63126 0+10- INS -13 36 10 NA|10 1.00 BreakDancerMini-0.0.1 q10

An 13 bp insertion detected by BreakDancerMini between chr1:62767 and chr1:63126 with 10 supporting read pairs from a single library 'NA' and a confidence score of 36.

Notes:
Real SV breakpoints are expected to reside within the predicted boundaries with a margin > the read length.

'''

valid_breakdancer_svs = set(["DEL", "INS"])
breakdancer_source = set(["BreakDancer"])


class BreakDancerHeader:
    def __init__(self):
        self.header_dict = {}

    def parse_header_line(self, header_line):
        if header_line.startswith("#Software:"):
            self.header_dict["software"] = header_line.split()[1]
        elif header_line.startswith("#Command:"):
            self.header_dict["command"] = header_line.split()[1:]
        elif not (header_line.startswith("#Library") or header_line.startswith("#Chr1")):
            fields = header_line[1:].split()
            self.header_dict[fields[0]] = dict(field.split(":") for field in fields[1:])
        logger.info(self.header_dict)

    def __str__(self):
        return str(self.__dict__)


class BreakDancerRecord:
    def __init__(self, record_string):
        fields = record_string.split()
        self.chr1 = fields[0]
        self.pos1 = int(fields[1])
        self.ori1 = fields[2]
        self.chr2 = fields[3]
        self.pos2 = int(fields[4])
        self.ori2 = fields[5]
        self.sv_type = fields[6]
        self.sv_len = int(fields[7])
        self.score = float(fields[8])
        self.supporting_read_pairs = int(fields[9])
        self.supporting_reads_pairs_lib = dict(
            map(lambda l: (l[0], int(l[1])), (s.split("|") for s in fields[10].split(":"))))

    def __str__(self):
        return str(self.__dict__)

    def __repr__(self):
        return "<" + self.__class__.__name__ + " " + str(self.__dict__) + ">"

    def to_sv_interval(self):
        if self.sv_type not in valid_breakdancer_svs: return None

        if self.sv_type == "DEL":
            return SVInterval(self.chr1, self.pos1, self.pos1 + abs(self.sv_len), sv_type=self.sv_type,
                              length=self.sv_len, sources=breakdancer_source,
                              cipos=[0, self.pos2 - self.pos1 - abs(self.sv_len)], native_sv=self)
        else:
            return SVInterval(self.chr1, self.pos1, self.pos1, sv_type="INS", length=self.sv_len,
                              sources=breakdancer_source, cipos=[0, self.pos2 - self.pos1], native_sv=self)


class BreakDancerReader:
    def __init__(self, file_name):
        logger.info("File is " + file_name)
        self.file_fd = open(file_name)
        self.header = BreakDancerHeader()

    def __iter__(self):
        return self

    def next(self):
        while True:
            line = self.file_fd.next()
            if line[0] != "#":
                return BreakDancerRecord(line.strip())
            else:
                self.header.parse_header_line(line.strip())

    def get_header(self):
        return self.header
