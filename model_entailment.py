import sys
import numpy
import gzip
import argparse
import pickle
from index_data import DataProcessor
from onto_attention import OntoAttentionLSTM
from keras.models import Model, model_from_yaml
from keras.layers import Activation, Dense, Dropout, Embedding, Input, LSTM, merge
from keras_extensions import HigherOrderEmbedding
from keras.callbacks import EarlyStopping

class EntailmentModel(object):
    def __init__(self, num_senses=2, num_hyps=5, word_dim=50, embed_file=None):
        self.dp = DataProcessor(word_syn_cutoff=num_senses, syn_path_cutoff=num_hyps)
        #self.max_hyps_per_word = num_senses * num_hyps
        self.num_senses = num_senses
        self.num_hyps = num_hyps
        self.numpy_rng = numpy.random.RandomState(12345)
        self.word_rep = {}
        self.word_dim = word_dim
        self.model = None

    # TODO: Make an abstract entailmant model class, and inherit LSTMEntailmentModel and OntoLSTMEntailmentModel classes each of owhich reimplements train function.
    def train(self, S1_ind, S2_ind, C1_ind, C2_ind, label_ind, num_label_types, ontoLSTM=False, use_attention=False, num_epochs=20, mlp_size=1024, embedding=None, tune_embedding=True):
        assert S1_ind.shape == S2_ind.shape
        assert C1_ind.shape == C2_ind.shape
        num_words = len(self.dp.word_index)
        num_syns = len(self.dp.synset_index)
        length = C1_ind.shape[1]
        label_onehot = numpy.zeros((len(label_ind), num_label_types))
        for i, ind in enumerate(label_ind):
            label_onehot[i][ind] = 1.0
        early_stopping = EarlyStopping(monitor='val_acc', patience=1)
        if ontoLSTM:
            print >>sys.stderr, "Using OntoLSTM"
            if tune_embedding:
                sent1 = Input(name='sent1', shape=C1_ind.shape[1:], dtype='int32')
                sent2 = Input(name='sent2', shape=C2_ind.shape[1:], dtype='int32')
                model_inputs = [sent1, sent2]
                if embedding is None:
                    embedding_layer = HigherOrderEmbedding(input_dim=num_syns, output_dim=self.word_dim, name='embedding', mask_zero=True)
                else:
                    embedding_layer = HigherOrderEmbedding(input_dim=num_syns, output_dim=self.word_dim, weights=[embedding], name='embedding', mask_zero=True)
                sent1_embedding = embedding_layer(sent1)
                sent2_embedding = embedding_layer(sent2)
            else:
                assert embedding is not None, "If you wish to fix the embedding (tune_embedding == False), initialize it (embedding should not be None)"
                embed_dim = embedding.shape[1]
                sent1_embedding = Input(name='sent1_embedding', shape=(C1_ind.shape[1:], C1_ind.shape[2], embed_dim))
                sent1_embedding = Input(name='sent2_embedding', shape=(C2_ind.shape[1:], C2_ind.shape[2], embed_dim))
                model_inputs = [sent1_embedding, sent2_embedding]
            sent1_dropout = Dropout(0.5)(sent1_embedding)
            sent2_dropout = Dropout(0.5)(sent2_embedding)
            lstm = OntoAttentionLSTM(input_dim=self.word_dim, output_dim=self.word_dim/2, input_length=length, num_senses=self.num_senses, num_hyps=self.num_hyps, use_attention=use_attention, name='sent_lstm')
            sent1_lstm_output = lstm(sent1_dropout)
            sent2_lstm_output = lstm(sent2_dropout)
            sent1_lstm_output_dropout = Dropout(0.2)(sent1_lstm_output)
            sent2_lstm_output_dropout = Dropout(0.2)(sent2_lstm_output)
            concat_sent_rep = merge([sent1_lstm_output_dropout, sent2_lstm_output_dropout], mode='concat')
            mul_sent_rep = merge([sent1_lstm_output_dropout, sent2_lstm_output_dropout], mode='mul')
            diff_sent_rep = merge([sent1_lstm_output_dropout, sent2_lstm_output_dropout], mode=lambda l: l[0]-l[1], output_shape=lambda l:l[0])
            merged_sent_rep = merge([concat_sent_rep, mul_sent_rep, diff_sent_rep], mode='concat')
            relu1 = Dense(output_dim=mlp_size, activation='relu')
            relu2 = Dense(output_dim=mlp_size, activation='relu')
            softmax = Dense(output_dim=num_label_types, activation='softmax')
            label_probs = softmax(relu2(relu1(merged_sent_rep)))
            model = Model(input=model_inputs, output=label_probs)
            print >>sys.stderr, model.summary()
            model.compile(optimizer='adam', loss='categorical_crossentropy', metrics=['accuracy'])
            data_size = C1_ind.shape[0]
            #train_size = int(data_size * 0.9)
            train_size = max(data_size - 10000, 0.9*data_size)
            model.fit([C1_ind[:train_size], C2_ind[:train_size]], label_onehot[:train_size], nb_epoch=num_epochs, validation_data=([C1_ind[train_size:], C2_ind[train_size:]], label_onehot[train_size:]), callbacks=[early_stopping])
            self.model = model
        else:
            print >>sys.stderr, "Using traditional LSTM"
            sent1 = Input(name='sent1', shape=S1_ind.shape[1:], dtype='int32')
            sent2 = Input(name='sent2', shape=S2_ind.shape[1:], dtype='int32')
            embedding_layer = Embedding(input_dim=num_words, output_dim=self.word_dim, name='embedding', mask_zero=True)
            sent1_embedding = embedding_layer(sent1)
            sent2_embedding = embedding_layer(sent2)
            sent1_dropout = Dropout(0.5)(sent1_embedding)
            sent2_dropout = Dropout(0.5)(sent2_embedding)
            lstm = LSTM(input_dim=self.word_dim, output_dim=self.word_dim/2, input_length=length, name='sent_lstm')
            sent1_lstm_out = lstm(sent1_dropout)
            sent2_lstm_out = lstm(sent2_dropout)
            sent1_lstm_output_dropout = Dropout(0.2)(sent1_lstm_out)
            sent2_lstm_output_dropout = Dropout(0.2)(sent2_lstm_out)
            concat_sent_rep = merge([sent1_lstm_output_dropout, sent2_lstm_output_dropout], mode='concat')
            mul_sent_rep = merge([sent1_lstm_output_dropout, sent2_lstm_output_dropout], mode='mul')
            diff_sent_rep = merge([sent1_lstm_output_dropout, sent2_lstm_output_dropout], mode=lambda l: l[0]-l[1], output_shape=lambda l:l[0])
            merged_sent_rep = merge([concat_sent_rep, mul_sent_rep, diff_sent_rep], mode='concat')
            relu1 = Dense(output_dim=mlp_size, activation='relu')
            relu2 = Dense(output_dim=mlp_size, activation='relu')
            softmax = Dense(output_dim=num_label_types, activation='softmax')
            label_probs = softmax(relu2(relu1(merged_sent_rep)))
            model = Model(input=[sent1, sent2], output=label_probs)
            print >>sys.stderr, model.summary()
            model.compile(optimizer='adam', loss='categorical_crossentropy', metrics=['accuracy'])
            data_size = S1_ind.shape[0]
            #train_size = int(data_size * 0.9)
            train_size = max(data_size - 10000, 0.9*data_size)
            model.fit([S1_ind[:train_size], S2_ind[:train_size]], label_onehot[:train_size], nb_epoch=num_epochs, validation_data=([S1_ind[train_size:], S2_ind[train_size:]], label_onehot[train_size:]), callbacks=[early_stopping])
            self.model = model
    # TODO: Generalize test method to take an array of inputs alone, to make it general enough to put it in the abstract class.
    def test(self, label_ind_test, use_onto_lstm, S1_ind_test=None, S2_ind_test=None, C1_ind_test=None, C2_ind_test=None, num_label_types=3):
        if not self.model:
            raise RuntimeError, "Model not trained!"
        label_onehot_test = numpy.zeros((len(label_ind_test), num_label_types))
        for i, ind in enumerate(label_ind_test):
            label_onehot_test[i][ind] = 1.0
        if use_onto_lstm:
            test_metrics = self.model.evaluate([C1_ind_test, C2_ind_test], label_onehot_test)
            print >>sys.stderr, "Test accuracy: %.4f"%(test_metrics[1])
            predictions = numpy.argmax(self.model.predict([C1_ind_test, C2_ind_test]), axis=1)
        else:
            test_metrics = self.model.evaluate([S1_ind_test, S2_ind_test], label_onehot_test)
            print >>sys.stderr, "Test accuracy: %.4f"%(test_metrics[1])
            predictions = numpy.argmax(self.model.predict([S1_ind_test, S2_ind_test]), axis=1)
        return predictions

    def get_attention(self, C_ind, embedding=None):
        if not self.model:
            raise RuntimeError, "Model not trained!"
        embedding_given = False if embedding is None else True
        model_embedding = None
        model_lstm = None
        for layer in self.model.layers:
            if layer.name == "embedding":
                model_embedding = layer
            if layer.name == "sent_lstm":
                model_lstm = layer
        if not (model_embedding or embedding_given) or not model_lstm:
            raise RuntimeError, "Did not find the layers expected"
        lstm_weights = model_lstm.get_weights()
        import pickle
        pkl_file = open("lstm_weights.pkl", "wb")
        pickle.dump(lstm_weights, pkl_file)
        pkl_file.close()
        if not embedding_given:
            sent = Input(shape=C_ind.shape[1:], dtype='int32')
            embedding_weights = model_embedding.get_weights()
            embed_in_dim, embed_out_dim = embedding_weights[0].shape
            att_embedding = HigherOrderEmbedding(input_dim=embed_in_dim, output_dim=embed_out_dim, weights=embedding_weights)
            sent_embedding = att_embedding(sent)
            att_input = sent
        else:
            _, embed_out_dim = embedding.shape
            sent_embedding = Input(shape=(C_ind.shape[1], C_ind.shape[2], embed_out_dim))
            att_input = sent_embedding
        onto_lstm = OntoAttentionLSTM(input_dim=embed_out_dim, output_dim=embed_out_dim/2, input_length=model_lstm.input_length, num_senses=self.num_senses, num_hyps=self.num_hyps, use_attention=True, return_attention=True, weights=lstm_weights)
        att_output = onto_lstm(sent_embedding)
        att_model = Model(input=att_input, output=att_output)
        att_model.compile(optimizer='adam', loss='mse') # optimizer and loss are not needed since we are not going to train this model.
        C_att = att_model.predict(C_ind) if not embedding_given else att_model.predict(embedding[C_ind])
        print >>sys.stderr, "Got attention values. Input, output shapes:", C_ind.shape, C_att.shape
        return C_att

if __name__ == "__main__":
    argparser = argparse.ArgumentParser(description="Train entailment model using ontoLSTM or traditional LSTM")
    argparser.add_argument('--train_file', type=str, help="TSV file with label, premise, hypothesis in three columns")
    argparser.add_argument('--repfile', type=str, help="Gzipped word embedding file")
    argparser.add_argument('--word_dim', type=int, help="Word/Synset vector size", default=50)
    argparser.add_argument('--use_onto_lstm', help="Use ontoLSTM. If this flag is not set, will use traditional LSTM", action='store_true')
    argparser.add_argument('--num_senses', type=int, help="Number of senses per word if using OntoLSTM (default 2)", default=2)
    argparser.add_argument('--num_hyps', type=int, help="Number of hypernyms per sense if using OntoLSTM (default 5)", default=5)
    argparser.add_argument('--use_attention', help="Use attention in ontoLSTM. If this flag is not set, will use average concept representations", action='store_true')
    argparser.add_argument('--test_file', type=str, help="Optionally provide test file for which accuracy will be computed")
    argparser.add_argument('--attention_output', type=str, help="Print attention values of the validation data in the given file")
    argparser.add_argument('--synset_embedding', type=str, help="File with synset vectors")
    argparser.add_argument('--fix_embedding', help="File with synset vectors", action='store_true')
    argparser.add_argument('--num_epochs', type=int, help="Number of epochs (default 20)", default=20)
    args = argparser.parse_args()
    use_synset_embedding = False
    vec_max = -float("inf")
    vec_min = float("inf")
    if args.synset_embedding:
        synset_embedding = {}
        for line in gzip.open(args.synset_embedding):
            ln_parts = line.strip().split()
            if len(ln_parts) == 2:
                continue
            word = ln_parts[0]
            vec = numpy.asarray([float(f) for f in ln_parts[1:]])
            vec_max = max(max(vec), vec_max)
            vec_min = min(min(vec), vec_min)
            synset_embedding[word] = vec
        vec_dim = len(vec)
        use_synset_embedding = True
    em = EntailmentModel(num_senses=args.num_senses, num_hyps=args.num_hyps, word_dim=args.word_dim, embed_file=args.repfile)
    tagged_sentences = []
    label_map = {}
    label_ind = []
    sentlenlimit = None
    do_test = False
    do_train = False
    S1_ind_test = None
    S2_ind_test = None
    C1_ind_test = None
    C2_ind_test = None
    label_ind_test = None
    max_test_sentlen = 0
    model_name_prefix = "ent_model_ontolstm=%s_att=%s_senses=%d_hyps=%d"%(str(args.use_onto_lstm), str(args.use_attention), args.num_senses, args.num_hyps)
    
    if args.test_file is not None:
        print >>sys.stderr, "Reading test data"
        tagged_sentences_test = []
        label_ind_test = []
        for line in open(args.test_file):
            lnstrp = line.strip()
            label, tagged_sentence = lnstrp.split("\t")
            if label not in label_map:
                label_map[label] = len(label_map)
            label_ind_test.append(label_map[label])
            test_sentlen = len(tagged_sentence.split())
            if test_sentlen > max_test_sentlen:
                max_test_sentlen = test_sentlen
            tagged_sentences_test.append(tagged_sentence)
        do_test = True
    if args.train_file is not None:
        print >>sys.stderr, "Reading training data"
        max_train_sentlen = 0
        for line in open(args.train_file):
            lnstrp = line.strip()
            label, tagged_sentence = lnstrp.split("\t")
            if label not in label_map:
                label_map[label] = len(label_map)
            label_ind.append(label_map[label])
            train_sentlen = len(tagged_sentence.split())
            if train_sentlen > max_train_sentlen:
                max_train_sentlen = train_sentlen
            tagged_sentences.append(tagged_sentence)
        max_sentlen = max(max_train_sentlen, max_test_sentlen)
        print >>sys.stderr, "Indexing training data"
        _, (S1_ind, S2_ind), (C1_ind, C2_ind) = em.dp.read_sentences(tagged_sentences, sentlenlimit=max_sentlen)
        do_train = True
    else:
        print >>sys.stderr, "Loading stored model"
        em.model = model_from_yaml(open("%s.yaml"%model_name_prefix).read(), custom_objects={"HigherOrderEmbedding": HigherOrderEmbedding, "OntoAttentionLSTM": OntoAttentionLSTM})
        print >>sys.stderr, em.model.summary()
        em.model.load_weights("%s.h5"%model_name_prefix)
        em.model.compile(optimizer='adam', loss='categorical_crossentropy', metrics=['accuracy'])
        dataproc_pkl_file = open("%s_dataproc.pkl"%model_name_prefix)
        em.dp = pickle.load(dataproc_pkl_file)
        max_sentlen = em.model.get_input_shape_at(0)[0][1]

    if do_test:
        print >>sys.stderr, "Indexing test data"
        _, (S1_ind_test, S2_ind_test), (C1_ind_test, C2_ind_test) = em.dp.read_sentences(tagged_sentences_test, sentlenlimit=max_sentlen)
    

    if do_train:
        print >>sys.stderr, "Training on provided data"
        if use_synset_embedding:
            ind_synset_embedding = em.numpy_rng.uniform(low=vec_min, high=vec_max, size=(len(em.dp.synset_index), vec_dim))
            for syn in em.dp.synset_index:
                if syn in synset_embedding:
                    ind_synset_embedding[em.dp.synset_index[syn]] = synset_embedding[syn]
            print >>sys.stderr, "Using pretrained synset embeddings"
            em.train(S1_ind, S2_ind, C1_ind, C2_ind, label_ind, len(label_map), ontoLSTM=args.use_onto_lstm, use_attention=args.use_attention, num_epochs=args.num_epochs, embedding=ind_synset_embedding)
        else: 
            print >>sys.stderr, "Will learn synset embeddings"
            em.train(S1_ind, S2_ind, C1_ind, C2_ind, label_ind, len(label_map), ontoLSTM=args.use_onto_lstm, use_attention=args.use_attention, num_epochs=args.num_epochs)
    
        model_yaml_string = em.model.to_yaml()
        open("%s.yaml"%model_name_prefix, "w").write(model_yaml_string)
        em.model.save_weights("%s.h5"%model_name_prefix, overwrite=True)
        dataproc_pkl_file = open("%s_dataproc.pkl"%model_name_prefix, "w")
        pickle.dump(em.dp, dataproc_pkl_file)
    if do_test:
        predictions = em.test(label_ind_test, use_onto_lstm=args.use_onto_lstm, S1_ind_test=S1_ind_test, S2_ind_test=S2_ind_test, C1_ind_test=C1_ind_test, C2_ind_test=C2_ind_test)
        rev_label_map = {v:k for k, v in label_map.items()}
        test_outfile = open("%s.out"%model_name_prefix, "w")
        for pred in predictions:
            print >>test_outfile, rev_label_map[pred] 
        test_outfile.close()
    if args.attention_output is not None:
        if not do_test:
            raise RuntimeError, "Provide a test file"
        rev_synset_ind = {ind: syn for (syn, ind) in em.dp.synset_index.items()}
        C_ind = numpy.concatenate([C1_ind_test, C2_ind_test])
        C_att = em.get_attention(C_ind, ind_synset_embedding) if args.fix_embedding else em.get_attention(C_ind) 
        C1_att, C2_att = numpy.split(C_att, 2)
        # Concatenate sentence 1 and 2 in each data point
        C_sj_ind = numpy.concatenate([C1_ind_test, C2_ind_test], axis=1)
        C_sj_att = numpy.concatenate([C1_att, C2_att], axis=1)
        outfile = open(args.attention_output, "w")
        for i, (sent, sent_c_inds, sent_c_atts) in enumerate(zip(tagged_sentences_test, C_sj_ind, C_sj_att)):
            print >>outfile, "SENT %d: %s"%(i, sent)
            words = sent.replace(" |||", "").split()
            word_id = 0
            for word_c_inds, word_c_atts in zip(sent_c_inds, sent_c_atts):
                if word_c_inds.sum() == 0:
                    continue
                sense_id = 0
                print >>outfile, "Attention for %s"%(words[word_id])
                word_id += 1
                for s_h_ind, s_h_att in zip(word_c_inds, word_c_atts):
                    if sum(s_h_ind) == 0:
                        continue
                    print >>outfile, "\nSense %d"%(sense_id)
                    sense_id += 1
                    for h_ind, h_att in zip(s_h_ind, s_h_att):
                        print >>outfile, rev_synset_ind[h_ind], h_att 
                print >>outfile
            print >>outfile
